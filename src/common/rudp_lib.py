import struct

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
HEADER_FORMAT = "!IIHHB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # Size of our header (13 bytes)


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
    temp_header = struct.pack(HEADER_FORMAT, seq_num, ack_num, 0, window_size, flags)

    # 2. Calculate checksum over the whole packet (header + data)
    checksum = calculate_checksum(temp_header + data)

    # 3. Create final header with the calculated checksum
    header = struct.pack(HEADER_FORMAT, seq_num, ack_num, checksum, window_size, flags)

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
    seq_num, ack_num, rx_checksum, window_size, flags = struct.unpack(
        HEADER_FORMAT, header
    )

    # Verify checksum
    # Reconstruct header with 0 to match creation logic
    temp_header = struct.pack(HEADER_FORMAT, seq_num, ack_num, 0, window_size, flags)
    cal_checksum = calculate_checksum(temp_header + data)

    if rx_checksum != cal_checksum:
        # raise ValueError("Packet corruption detected! Checksum mismatch.")
        return None  # Corrupted

    return seq_num, ack_num, flags, window_size, data
