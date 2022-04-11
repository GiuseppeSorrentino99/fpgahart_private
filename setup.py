from setuptools import find_packages, setup

setup(
    name="fpgaHART",
    version="0.1.0",
    description="FPGA toolflow for Human Action Recognition",
    author="Petros Toupas",
    author_email="ptoupas@gmail.com",
    maintainer="Petros Toupas",
    maintainer_email="ptoupas@gmail.com",
    packages=find_packages(exclude=("model_analysis", "models")),
    keywords="computer vision, video understanding",
    include_package_data=True,
    url="https://github.com/ptoupas/fpgaHART",
    license="GNU General Public License v3.0",
    zip_safe=False,
)
