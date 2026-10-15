"""Explicit, tracked upgrade for employee referrals and ERP draft orders."""


def execute():
    # The account metadata and portal installer are additive and idempotent.
    # Keep the original accounts patch immutable; existing sites have already run it.
    from catalog_sync.accounts.setup import install

    install()
