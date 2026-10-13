"""Add website registration metadata on upgrade without changing product exports."""


def execute():
    from catalog_sync.accounts.setup import install

    install()
