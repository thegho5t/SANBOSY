for path in ["/etc/passwd", "/newfile", "../escape", "/usr/evil"]:
    try:
        with open(path, "w") as f:
            f.write("x")
        print("WROTE", path, "-- FAIL")
    except Exception as e:
        print("write blocked", path, ":", type(e).__name__)
try:
    with open("/box/ok", "w") as f:
        f.write("box is writable")
    print("box writable: OK")
except Exception as e:
    print("box write FAILED:", e)
