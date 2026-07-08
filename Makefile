# Common operations. Run inside WSL2 (the sandbox needs runsc + systemd --user).
.PHONY: help setup install-test test test-unit test-integration hostile run \
        service-install service-logs clean-cache clean-runs

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

setup:  ## install runsc + build the rootfs with all detected toolchains
	bash scripts/setup_wsl2.sh

install-test:  ## install the test dependencies
	python3 -m pip install --user --break-system-packages -e '.[test]'

test:  ## run the whole pytest suite (needs runsc for integration)
	python3 -m pytest -q

test-unit:  ## run the fast unit tests only (no gVisor; CI path)
	python3 -m pytest -q -m "not integration"

test-integration:  ## run only the sandboxed end-to-end tests
	python3 -m pytest -q -m integration

hostile:  ## run the standalone containment suite
	python3 tests/run_hostile.py

run:  ## start the API + UI from a login shell (needed for systemd --user)
	bash -lic 'exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000'

service-install:  ## install & start the systemd --user service
	mkdir -p ~/.config/systemd/user
	cp deploy/sandbox.service ~/.config/systemd/user/sandbox.service
	systemctl --user daemon-reload
	systemctl --user enable --now sandbox
	@echo "installed. logs: make service-logs"

service-logs:  ## follow the service logs
	journalctl --user -u sandbox -f

clean-cache:  ## clear the persistent build caches
	rm -rf ~/.sandbox/cache

clean-runs:  ## clear any leftover run dirs and history
	rm -rf ~/.sandbox/runs ~/.sandbox/history.db*
