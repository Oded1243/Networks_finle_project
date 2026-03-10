# --- Constants to avoid "magic numbers" (Magic Numbers) --- (This code was created with AI)
# Flags - We use bits (Bitwise) so we can combine flags (for example ACK + FIN)
FLAG_NONE = 0b00000000
FLAG_SYN = 0b00000001  # Connection request
FLAG_ACK = 0b00000010  # Receipt confirmation
FLAG_FIN = 0b00000100  # End of connection
FLAG_DATA = 0b00001000  # Data packet

# Size definitions
# UDP is limited to 65535 bytes. We'll take a safety margin and set max data size to 60,000.
MAX_PAYLOAD_SIZE = 60000
# Header: Seq(4), Ack(4), Checksum(2), Window(2), Flags(1)
# Total size: 4 + 4 + 2 + 2 + 1 = 13 bytes
HEADER_SIZE = 13


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
    # 1. Create header with checksum = 0
    temp_header = (
        _pack_uint32_be(seq_num)
        + _pack_uint32_be(ack_num)
        + _pack_uint16_be(0)
        + _pack_uint16_be(window_size)
        + _pack_uint8(flags)
    )

    # 2. Calculate checksum over the whole packet (header + data)
    checksum = calculate_checksum(temp_header + data)

    # 3. Create final header with the calculated checksum
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
        # raise ValueError("Packet is too small to contain a valid RUDP header.")
        return None  # Return None for corruption/invalid

    # Extract the header from the packet
    header = packet_bytes[:HEADER_SIZE]
    data = packet_bytes[HEADER_SIZE:]

    # Unpack to get the received checksum
    seq_num = _unpack_uint32_be(header[0:4])
    ack_num = _unpack_uint32_be(header[4:8])
    rx_checksum = _unpack_uint16_be(header[8:10])
    window_size = _unpack_uint16_be(header[10:12])
    flags = _unpack_uint8(header[12:13])

    # Verify checksum
    # Reconstruct header with 0 to match creation logic
    temp_header = (
        _pack_uint32_be(seq_num)
        + _pack_uint32_be(ack_num)
        + _pack_uint16_be(0)
        + _pack_uint16_be(window_size)
        + _pack_uint8(flags)
    )
    cal_checksum = calculate_checksum(temp_header + data)

    if rx_checksum != cal_checksum:
        # raise ValueError("Packet corruption detected! Checksum mismatch.")
        return None  # Corrupted

    return seq_num, ack_num, flags, window_size, data
