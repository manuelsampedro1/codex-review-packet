test:
	python3 -m unittest discover -s tests

build:
	python3 -m py_compile codex_review_packet.py

lint:
	python3 -m py_compile codex_review_packet.py
