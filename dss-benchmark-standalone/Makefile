.PHONY: eval eval-fast compile install clean matrix leaderboard

PYTHON := python3

install:
	$(PYTHON) -m pip install -r requirements.txt

compile:
	find . -name "*.py" -print0 | xargs -0 $(PYTHON) -m py_compile

eval: compile
	$(PYTHON) harness/runner.py --adapter faiss

eval-fast: compile
	$(PYTHON) harness/runner.py --adapter faiss --mock-embeddings --seeds 42

matrix:
	$(PYTHON) scripts/run_matrix.py
	$(PYTHON) scripts/generate_leaderboard.py

leaderboard:
	$(PYTHON) scripts/generate_leaderboard.py

clean:
	rm -rf eval/reports __pycache__ .pytest_cache
	rm -f $(shell find . -name "*.pyc")
