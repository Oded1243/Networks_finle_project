FLAG_NONE = 0b00000000
FLAG_SYN = 0b00000001
FLAG_ACK = 0b00000010
FLAG_FIN = 0b00000100
FLAG_DATA = 0b00001000

MAX_PAYLOAD_SIZE = 60000
HEADER_SIZE = 13  # Seq(4) + Ack(4) + Checksum(2) + Window(2) + Flags(1)


def _pack_uint32_be(value):
    """Pack a 32-bit unsigned integer as big-endian bytes."""
    return value.to_bytes(4, "big")


def _pack_uint16_be(value):
    """Pack a 16-bit unsigned integer as big-endian bytes."""
    return value.to_bytes(2, "big")


def _pack_uint8(value):
    """Pack a 8-bit unsigned integer as bytes."""
    return bytes([value & 0xFF])


def _unpack_uint32_be(data):
    """Unpack big-endian bytes to 32-bit unsigned integer."""
    return int.from_bytes(data[:4], "big")


def _unpack_uint16_be(data):
    """Unpack big-endian bytes to 16-bit unsigned integer."""
    return int.from_bytes(data[:2], "big")


def _unpack_uint8(data):
    """Unpack bytes to 8-bit unsigned integer."""
    return data[0] if isinstance(data[0], int) else ord(data[0])


def calculate_checksum(data):
    """Calculates a simple 16-bit checksum."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return sum(data) & 0xFFFF


def create_packet(seq_num, ack_num, flags, window_size=2048, data=b""):
    """
    Packs the data and header into one RUDP packet.
    Includes Checksum and Window Size.
    """
    temp_header = (
        _pack_uint32_be(seq_num)
        + _pack_uint32_be(ack_num)
        + _pack_uint16_be(0)
        + _pack_uint16_be(window_size)
        + _pack_uint8(flags)
    )

    checksum = calculate_checksum(temp_header + data)

    header = (
        _pack_uint32_be(seq_num)
        + _pack_uint32_be(ack_num)
        + _pack_uint16_be(checksum)
        + _pack_uint16_be(window_size)
        + _pack_uint8(flags)
    )

    return header + data


def parse_packet(packet_bytes):
    """
    Unpacks a received RUDP packet into header and data.
    Verifies Checksum.
    Returns: (seq_num, ack_num, flags, window_size, data) or None if corrupted.
    """
    if len(packet_bytes) < HEADER_SIZE:
        return None

    header = packet_bytes[:HEADER_SIZE]
    data = packet_bytes[HEADER_SIZE:]

    seq_num = _unpack_uint32_be(header[0:4])
    ack_num = _unpack_uint32_be(header[4:8])
    rx_checksum = _unpack_uint16_be(header[8:10])
    window_size = _unpack_uint16_be(header[10:12])
    flags = _unpack_uint8(header[12:13])

    temp_header = (
        _pack_uint32_be(seq_num)
        + _pack_uint32_be(ack_num)
        + _pack_uint16_be(0)
        + _pack_uint16_be(window_size)
        + _pack_uint8(flags)
    )
    cal_checksum = calculate_checksum(temp_header + data)

    if rx_checksum != cal_checksum:
        return None

    return seq_num, ack_num, flags, window_size, data
