import os

# Try to fill the writable workdir far beyond its size cap.
def bomb(path):
    try:
        with open(path, "wb") as f:
            chunk = b"A" * (1024 * 1024)  # 1 MiB
            written = 0
            while written < 2 * 1024 * 1024 * 1024:  # attempt 2 GiB
                f.write(chunk)
                written += len(chunk)
        print("WROTE 2GB to", path, "-- FAIL")
    except OSError as e:
        print("disk write to", path, "capped:", e.__class__.__name__, os.strerror(e.errno) if e.errno else "")

bomb("/box/big")
bomb("/tmp/big")
print("host unaffected")
