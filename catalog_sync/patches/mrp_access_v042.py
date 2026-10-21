"""Install MRP defaults and explicit employee trade-price control."""


def execute():
    from catalog_sync.accounts.setup import install

    install()
