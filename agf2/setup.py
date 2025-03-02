from setuptools import setup, find_packages

setup(
    name="agf2",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[

    ],
    entry_points={
        'console_scripts': [
            'agf2=agf2:main',
        ],
    },
    author="Harrison Green",
    author_email="harrisonmichaelgreen@gmail.com",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)