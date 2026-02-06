from setuptools import setup, find_packages

setup(
    name="agfi",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[

    ],
    entry_points={
        'console_scripts': [
            'agfi=agfi:main',
        ],
    },
    author="",
    author_email="",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)