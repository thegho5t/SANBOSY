import os
n = 0
while True:
    try:
        os.fork()
        n += 1
    except OSError:
        print("fork failed after", n, "forks")
        break
