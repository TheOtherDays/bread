import StringIO, types, struct, collections, functools

LITTLE_ENDIAN = 0
BIG_ENDIAN = 1

STRUCT_CONVERSION_SYMBOLS = {
    (8, True) : 'b',
    (8, False) : 'B',
    (16, True) : 'h',
    (16, False) : 'H',
    (32, True) : 'i',
    (32, False) : 'I',
    (64, True) : 'q',
    (64, False) : 'Q'
    }

def mask_bits(byte, start_bit, stop_bit):
    return (((byte << start_bit) & 0xff)
            >> (7 - stop_bit + start_bit)) & 0xff

def substring_bits(data, start_bit, end_bit):
    start_byte = start_bit / 8
    end_byte = end_bit / 8

    shift_amount = start_bit % 8

    byte_list = bytearray()

    for current_index in xrange(start_byte, end_byte):
        current_byte = data[current_index]
        next_byte = data[current_index + 1]

        first_byte_chunk = mask_bits(current_byte, shift_amount, 7)
        second_byte_chunk = mask_bits(next_byte, 0, shift_amount - 1)

        shifted_byte = (first_byte_chunk << shift_amount) | second_byte_chunk

        byte_list.append(shifted_byte)

    if (start_byte == end_byte) or (end_bit - start_bit + 1 > 8):
        byte_list.append(mask_bits(data[end_byte], shift_amount, end_bit % 8))

    return byte_list

class BitwiseReader(object):
    """Read from a data source, specifying read size in bits and returning a list
    of bytes.
    """

    def __init__(self, data_source):
        if type(data_source) == file:
            self.fp = data_source
        elif type(data_source) in (str, bytearray):
            self.fp = StringIO.StringIO(data_source)
        else:
            raise ValueError("Don't know how to handle data sources of the "
                             "given type")

        # Offset in the file, in bits
        self.offset = 0

        self.cached_bytes = None
        self.cached_range = None

    def read(self, bits):
        if bits == 0:
            return bytearray()

        start_byte = self.offset / 8
        end_byte = (self.offset + bits - 1) / 8

        if self.cached_range is None or start_byte > self.cached_range[1]:
            # Cache the entire range, since we currently don't have it
            self.cached_bytes = collections.deque(
                map(ord, self.fp.read(end_byte - start_byte + 1)))
            self.cached_range = [start_byte, end_byte]
        elif end_byte > self.cached_range[1]:
            # Extend the cache to include bytes up to the end byte
            self.cached_bytes.extend(map(
                ord, self.fp.read(end_byte - self.cached_range[1])))
            self.cached_range[1] = end_byte

        # Drop elements from the cache up to the first byte we need
        while self.cached_range[0] < start_byte:
            self.cached_bytes.popleft()
            self.cached_range[0] += 1

        assert len(self.cached_bytes) == (
            self.cached_range[1] - self.cached_range[0] + 1)

        start_cached_bit = self.offset - (self.cached_range[0] * 8)
        end_cached_bit = start_cached_bit + bits - 1

        self.offset += bits

        return substring_bits(
            self.cached_bytes, start_cached_bit, end_cached_bit)

    def close(self):
        self.fp.close()

def parse(data_source, spec, type_name='bread_struct'):
    reader = BitwiseReader(data_source)

    return parse_from_reader(reader, spec, type_name)

def parse_from_reader(reader, spec, type_name='bread_struct', **kwargs):
    offsets = {}
    length = 0
    parsed_dict = {}

    start_reader_offset = reader.offset

    global_options = {}

    for spec_line in spec:
        if type(spec_line) == dict:
            global_options = spec_line
        elif isinstance(spec_line, types.FunctionType):
            spec_line(reader, **global_options)
        elif len(spec_line) == 1:
            parse_function = spec_line[0]
            parse_function(reader, **global_options)
        else:
            field_name = spec_line[0]
            parse_function = spec_line[1]
            options = global_options

            if len(spec_line) == 3:
                options = global_options.copy()
                options.update(spec_line[2])

            offsets[field_name] = reader.offset

            if type(parse_function) == list:
                val = parse_from_reader(reader, parse_function, **options)
            else:
                val = parse_function(reader, **options)

            if val is not None:
                parsed_dict[field_name] = val

    parsed_dict["OFFSETS"] = type('bread_struct_offsets', (object, ), offsets)
    parsed_dict["LENGTH"] = reader.offset - start_reader_offset

    def my_length(self):
        return self.LENGTH

    parsed_type = type(type_name, (object,), parsed_dict)

    parsed_type.__len__ = types.MethodType(
        my_length, parsed_type, parsed_type.__class__)

    return parsed_type()

def make_integer_type(length, signed):
    struct_conversion_symbol = (
        STRUCT_CONVERSION_SYMBOLS[(length, signed)])

    def integer_type(reader, endianness = LITTLE_ENDIAN, offset = 0, **kwargs):
        conversion = ''

        if endianness == LITTLE_ENDIAN:
            conversion = '<'
        elif endianness == BIG_ENDIAN:
            conversion = '>'

        conversion += struct_conversion_symbol

        return struct.unpack_from(conversion, reader.read(length))[0] + offset

    return integer_type

uint8  = make_integer_type(length=8,  signed=False)
byte = uint8
uint16 = make_integer_type(length=16, signed=False)
uint32 = make_integer_type(length=32, signed=False)
uint64 = make_integer_type(length=64, signed=False)
int8   = make_integer_type(length=8,  signed=True)
int16  = make_integer_type(length=16, signed=True)
int32  = make_integer_type(length=32, signed=True)
int64  = make_integer_type(length=64, signed=True)

def make_sub_byte_type(length):
    upper_bound = 2 ** length

    def sub_byte_type_parser(reader, **kwargs):
        data = reader.read(length)[0]

        if data < 0 or data >= upper_bound:
            raise ValueError("Invalid bit value %d" % (data))
        else:
            return data

    return sub_byte_type_parser

bit = make_sub_byte_type(1)
semi_nibble = make_sub_byte_type(2)
nibble = make_sub_byte_type(4)

def string(length, **kwargs):
    def string_parser(reader, **kwargs):
        return struct.unpack("%ds" % (self.length), reader.read(length))

    return string_parser

def boolean(reader, **kwargs):
    return bool(reader.read(1)[0])

def padding(length):
    def pad_parser(reader, **kwargs):
        # Skip over bits
        reader.read(length)
        return None

    return pad_parser

def enum(length, values):
    subparser = make_integer_type(length=length, signed=False)

    def parser(reader, **kwargs):
        coded_value = subparser(reader)

        return values[coded_value]

def array(length, substruct):

    if type(substruct) == list:
        # Passed a nested struct, which should be parsed according to its spec
        subparse_function = functools.partial(parse_from_reader, spec=substruct)
    else:
        # Passed a parsing function; should just return whatever that thing
        # parses
        subparse_function = substruct

    def parser(reader, **kwargs):
        substructs = []

        for i in xrange(length):
            substructs.append(subparse_function(reader))

        return substructs

    return parser
