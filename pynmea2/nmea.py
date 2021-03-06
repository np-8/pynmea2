import re
import operator
from functools import reduce
from pynmea2.nmea_utils import getattr__

class ParseError(ValueError):
    def __init__(self, message, data):
        super(ParseError, self).__init__((message, data))

class SentenceTypeError(ParseError):
    pass

class ChecksumError(ParseError):
    pass


class NMEASentenceType(type):
    sentence_types = {}
    def __init__(cls, name, bases, dct):
        type.__init__(cls, name, bases, dct)
        base = bases[0]
        if base is object:
            return
        base.sentence_types[name] = cls
        cls.name_to_idx = dict((f[1], i) for i, f in enumerate(cls.fields))


# http://mikewatkins.ca/2008/11/29/python-2-and-3-metaclasses/
NMEASentenceBase = NMEASentenceType('NMEASentenceBase', (object,), {})


class NMEASentence(NMEASentenceBase):
    '''
    Base NMEA Sentence

    Parses and generates NMEA strings

    Examples:

    >>> s = NMEASentence.parse("$GPGGA,184353.07,1929.045,S,02410.506,E,1,04,2.6,100.00,M,-33.9,M,,0000*6D")
    >>> print(s)
    '''

    sentence_re = re.compile(r'''
        # start of string, optional whitespace, optional '$'
        ^\s*\$?

        # message (from '$' or start to checksum or end, non-inclusve)
        (?P<nmea_str>
            # sentence type identifier
            (?P<sentence_type>

                # proprietary sentence
                (P\w{3})|

                # query sentence, ie: 'CCGPQ,GGA'
                # NOTE: this should have no data
                (\w{2}\w{2}Q,\w{3})|

                # taker sentence, ie: 'GPGGA'
                (\w{2}\w{3},)
            )

            # rest of message
            (?P<data>[^*]*)

        )
        # checksum: *HH
        (?:[*](?P<checksum>[A-F0-9]{2}))?

        # optional trailing whitespace
        \s*[\r\n]*$
        ''', re.X | re.IGNORECASE)

    talker_re = \
        re.compile(r'^(?P<talker>\w{2})(?P<sentence>\w{3}),$')
    query_re = \
        re.compile(r'^(?P<talker>\w{2})(?P<listener>\w{2})Q,(?P<sentence>\w{3})$')
    proprietary_re = \
        re.compile(r'^P(?P<manufacturer>\w{3})$')

    name_to_idx = {}
    fields = ()

    @staticmethod
    def checksum(nmea_str):
        return reduce(operator.xor, map(ord, nmea_str), 0)

    @staticmethod
    def parse(line, check=False, GSV_signal_id=False, GRS_ids=False):
        '''
        parse(line)

        Parses a string representing a NMEA 0183 sentence, and returns a
        NMEASentence object

        Parameters
        ---------
        line: str
            The NMEA string to be parsed
        check: bool
            If True, checks the checksum.
        GSV_signal_id: bool
            If True, assume that GSV NMEA (4.10+) messages have
            signalId appended as described in
            https://www.u-blox.com/en/docs/UBX-18010854.
        GRS_ids: bool
            If True, assume that GRS NMEA (4.10+) messages have
            systemId and signalId appended as described in
            https://www.u-blox.com/en/docs/UBX-18010854.
        
        Raises
        ------
        * ParseError if the string could not be parsed
        * ChecksumError if the checksum is missing or if 
          the checksum did not match.
        * SentenceTypeError if the sentence type is not understood
        '''
        match = NMEASentence.sentence_re.match(line)
        if not match:
            raise ParseError('could not parse data', line)

        # pylint: disable=bad-whitespace
        nmea_str        = match.group('nmea_str')
        data_str        = match.group('data')
        checksum        = match.group('checksum')
        sentence_type   = match.group('sentence_type').upper()
        data            = data_str.split(',')

        if checksum:
            cs1 = int(checksum, 16)
            cs2 = NMEASentence.checksum(nmea_str)
            if cs1 != cs2 and check:
                raise ChecksumError(
                    'checksum does not match: %02X != %02X' % (cs1, cs2), data)
        elif check:
            raise ChecksumError(
                'strict checking requested but checksum missing', data)

        talker_match = NMEASentence.talker_re.match(sentence_type)
        if talker_match:
            talker = talker_match.group('talker')
            sentence = talker_match.group('sentence')
            cls = TalkerSentence.sentence_types.get(sentence)
            cls_kwargs = dict()
            if sentence == 'GSV':
                if GSV_signal_id:
                    cls_kwargs['fields'] = cls.fields[:len(data)-1] + (('GNSS Signal ID', 'signal_id'),)
            if sentence == 'GRS':
                if GRS_ids:
                    cls_kwargs['fields'] = cls.fields + (('GNSS System ID', 'system_id'), ('GNSS Signal ID', 'signal_id'))
            if not cls:
                # TODO instantiate base type instead of fail
                raise SentenceTypeError(
                    'Unknown sentence type %s' % sentence_type, line)
            return cls(talker, sentence, data, **cls_kwargs)

        query_match = NMEASentence.query_re.match(sentence_type)
        if query_match and not data_str:
            talker = query_match.group('talker')
            listener = query_match.group('listener')
            sentence = query_match.group('sentence')
            return QuerySentence(talker, listener, sentence)

        proprietary_match = NMEASentence.proprietary_re.match(sentence_type)
        if proprietary_match:
            manufacturer = proprietary_match.group('manufacturer')
            cls = ProprietarySentence.sentence_types.get(manufacturer, ProprietarySentence)
            return cls(manufacturer, data)

        raise ParseError(
            'could not parse sentence type: %r' % sentence_type, line)

    def __getattr__(self, name):
        #pylint: disable=invalid-name
        return getattr__(self, name, source='type')


    def __setattr__(self, name, value):
        #pylint: disable=invalid-name
        t = type(self)
        if name not in t.name_to_idx:
            return object.__setattr__(self, name, value)

        i = t.name_to_idx[name]
        self.data[i] = str(value)

    def __repr__(self):
        #pylint: disable=invalid-name
        r = []
        d = []
        for i, v in enumerate(self.data):
            if i >= len(self.fields):
                d.append(v)
                continue
            name = self.fields[i][1]
            try: 
                value = getattr(self, name)
            except AttributeError:
                continue
            r.append('%s=%r' % (name, value))

        return '<%s(%s)%s>' % (
            type(self).__name__,
            ', '.join(r),
            d and ' data=%r' % d or ''
        )

    def identifier(self):
        raise NotImplementedError

    def render(self, checksum=True, dollar=True, newline=False):
        res = self.identifier() + ','.join(self.data)
        if checksum:
            res += '*%02X' % NMEASentence.checksum(res)
        if dollar:
            res = '$' + res
        if newline:
            res += (newline is True) and '\r\n' or newline
        return res

    def __str__(self):
        return self.render()


class TalkerSentence(NMEASentence):
    sentence_types = {}
    def __init__(self, talker, sentence_type, data, **kwargs):
        self.talker = talker
        self.sentence_type = sentence_type
        self.data = list(data)

    def identifier(self):
        return '%s%s,' % (self.talker, self.sentence_type)


class QuerySentence(NMEASentence):
    sentence_types = {}
    def __init__(self, talker, listener, sentence_type):
        self.talker = talker
        self.listener = listener
        self.sentence_type = sentence_type
        self.data = []

    def identifier(self):
        return '%s%sQ,%s,' % (self.talker, self.listener, self.sentence_type)


class ProprietarySentence(NMEASentence):
    sentence_types = {}
    def __init__(self, manufacturer, data):
        self.manufacturer = manufacturer
        self.data = list(data)

    def identifier(self):
        return 'P%s' % (self.manufacturer)
