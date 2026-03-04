import struct

# --- קבועים למניעת "מספרי קסם" (Magic Numbers) --- (קוד זה נוצר בעזרת AI)
# דגלים (Flags) - נשתמש בביטים (Bitwise) כדי שנוכל לשלב דגלים (למשל ACK + FIN)
FLAG_NONE = 0b00000000
FLAG_SYN = 0b00000001  # בקשת התחברות
FLAG_ACK = 0b00000010  # אישור קבלה
FLAG_FIN = 0b00000100  # סיום התקשרות
FLAG_DATA = 0b00001000  # חבילת נתונים

# הגדרות גדלים
# UDP מוגבל ל-65535 בתים. ניקח שוליים ביטחון ונגדיר גודל מידע מקסימלי ל-60,000.
MAX_PAYLOAD_SIZE = 60000
HEADER_FORMAT = "!IIB"  # I = 4 bytes (unsigned int), B = 1 byte (unsigned char)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # גודל הכותרת שלנו (9 בתים)


def create_packet(seq_num, ack_num, flags, data=b""):
    """
    אורז את הנתונים והכותרת לחבילת RUDP אחת מוכנה לשליחה.
    (פונקציה זו נוצרה בעזרת AI)
    """
    # יצירת הכותרת לפי הפורמט: Sequence, ACK, Flags
    header = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags)
    return header + data


def parse_packet(packet_bytes):
    """
    מפרק חבילת RUDP שהתקבלה לכותרת ולנתונים.
    מחזיר: (seq_num, ack_num, flags, data)
    (פונקציה זו נוצרה בעזרת AI)
    """
    if len(packet_bytes) < HEADER_SIZE:
        raise ValueError("Packet is too small to contain a valid RUDP header.")

    # חיתוך הכותרת מתוך החבילה
    header = packet_bytes[:HEADER_SIZE]
    data = packet_bytes[HEADER_SIZE:]

    # פירוק הכותרת למשתנים
    seq_num, ack_num, flags = struct.unpack(HEADER_FORMAT, header)

    return seq_num, ack_num, flags, data
