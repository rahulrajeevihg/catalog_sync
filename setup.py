from setuptools import find_packages, setup

setup(
    name="catalog_sync",
    version="0.4.0",
    description="Catalogue exports and website account approval setup for Frappe v14",
    author="Catalogue Engineering",
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
)
