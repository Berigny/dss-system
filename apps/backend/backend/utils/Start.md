Setup:

  # Terminal 1
  cd /Users/davidberigny/Documents/GitHub/ds-backend-local
  make launch-local

  # Terminal 2
  cd /Users/davidberigny/Documents/GitHub/ds-middleware-local
  make launch-ui

  Optional (sync):

  # Terminal 3
  cd /Users/davidberigny/Documents/GitHub/ds-middleware-local
  make daemon

  Then test skill availability:

  openclaw skills list --eligible | rg dual_ledger