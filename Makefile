.PHONY: help install compile eval eval-fast matrix leaderboard clean

BENCHMARK_DIR := dss-benchmark-standalone

help:
	@echo "DSS-EVAL public benchmark repository"
	@echo ""
	@echo "Available targets:"
	@echo "  make install    install benchmark dependencies"
	@echo "  make compile    compile all benchmark Python files"
	@echo "  make eval       run the full deterministic benchmark suite"
	@echo "  make eval-fast  run a fast smoke pass (< 60 seconds)"
	@echo "  make matrix     run the adapter x suite x seed matrix"
	@echo "  make leaderboard regenerate the comparison table"
	@echo "  make clean      remove generated reports and caches"

install:
	$(MAKE) -C $(BENCHMARK_DIR) install

compile:
	$(MAKE) -C $(BENCHMARK_DIR) compile

eval:
	$(MAKE) -C $(BENCHMARK_DIR) eval

eval-fast:
	$(MAKE) -C $(BENCHMARK_DIR) eval-fast

matrix:
	$(MAKE) -C $(BENCHMARK_DIR) matrix

leaderboard:
	$(MAKE) -C $(BENCHMARK_DIR) leaderboard

clean:
	$(MAKE) -C $(BENCHMARK_DIR) clean
