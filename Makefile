# Convenience targets for the Daily OS bot + analytics warehouse.
# Override the interpreter if needed:  make test PYTHON=python3.11
PYTHON ?= python3

.DEFAULT_GOAL := help

.PHONY: help test bot warehouse dbt dashboard report

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

warehouse: ## Load Notion -> Snowflake, then build the dbt models
	$(PYTHON) el_notion.py
	$(MAKE) dbt

dbt:  ## Build dbt models + tests (loads .env for the connection)
	cd dbt && $(PYTHON) -c "from dotenv import load_dotenv; load_dotenv('../.env'); import subprocess,sys; sys.exit(subprocess.run(['dbt','build','--profiles-dir','.']).returncode)"

dashboard: ## Install + serve the Evidence dashboard locally
	cd evidence && npm install && npm run sources && npm run dev

report: ## Send the weekly funnel report to Telegram now
	$(PYTHON) funnel_report.py
