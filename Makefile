# Convenience targets for the Daily OS bot.
# Override the interpreter if needed:  make test PYTHON=python3.11
PYTHON ?= python3

.DEFAULT_GOAL := help

.PHONY: help test bot

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

test:  ## Run all unit-test suites
	$(PYTHON) test_classifier.py
	$(PYTHON) test_pipeline_state.py
	$(PYTHON) test_pipeline_ingest.py
	$(PYTHON) test_pipeline_backfill.py
	$(PYTHON) test_pipeline_interviews.py
	$(PYTHON) test_briefings.py
	$(PYTHON) test_inbox_events.py

bot:  ## Run the Telegram bot
	$(PYTHON) bot.py
