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
HEADER_FORMAT = "!IIB"  # I = 4 bytes (unsigned int), B = 1 byte (unsigned char)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # Size of our header (9 bytes)


def create_packet(seq_num, ack_num, flags, data=b""):
    """
    Packs the data and header into one RUDP packet ready for transmission.
    (This function was created with AI)
    """
    # Creating the header according to the format: Sequence, ACK, Flags
    header = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags)
    return header + data


def parse_packet(packet_bytes):
    """
    Unpacks a received RUDP packet into header and data.
    Returns: (seq_num, ack_num, flags, data)
    (This function was created with AI)
    """
    if len(packet_bytes) < HEADER_SIZE:
        raise ValueError("Packet is too small to contain a valid RUDP header.")

    # Extract the header from the packet
    header = packet_bytes[:HEADER_SIZE]
    data = packet_bytes[HEADER_SIZE:]

    # Unpack the header into variables
    seq_num, ack_num, flags = struct.unpack(HEADER_FORMAT, header)

    return seq_num, ack_num, flags, data
