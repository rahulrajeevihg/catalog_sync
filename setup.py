from setuptools import find_packages, setup

setup(
    name="catalog_sync",
    version="0.1.0",
    description="Permission checked, read-only catalogue exports for Frappe v14",
    author="Catalogue Engineering",
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
)
