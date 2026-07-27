.PHONY: eval eval-fast compile install clean

PYTHON := python3

install:
	$(PYTHON) -m pip install -r requirements.txt

compile:
	find . -name "*.py" -print0 | xargs -0 $(PYTHON) -m py_compile

eval: compile
	$(PYTHON) harness/runner.py --adapter faiss

eval-fast: compile
	$(PYTHON) harness/runner.py --adapter faiss --mock-embeddings --seeds 42

clean:
	rm -rf eval/reports __pycache__ .pytest_cache
	rm -f $(shell find . -name "*.pyc")
