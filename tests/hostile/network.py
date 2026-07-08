import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(("1.1.1.1", 80))
    print("NETWORK OPEN -- FAIL")
except Exception as e:
    print("network blocked:", type(e).__name__, e)
