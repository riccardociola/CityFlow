# 🚲 CityFlow

**Predicting bike-sharing station shortages using machine learning.**

CityFlow is a data science project aimed at predicting whether a bike-sharing station will have **two or fewer bikes available one hour into the future**.

The project explores data preparation, time-series analysis, feature engineering, classification models, and model evaluation.

## Dataset

We use the **European Bike-Sharing Dataset**, containing historical data from 267 bike-sharing systems across Europe.

- **[Download the complete dataset (ZIP, ~2.3 GB)](https://syncandshare.lrz.de/getlink/fiSKzE1DtkXwB3AUWFFQgy/dataset.zip)**
- [Original GitHub repository](https://github.com/TUMFTM/european-bike-sharing-dataset)

Download and extract the dataset into `data/raw/`.

**Reference:** Waldner et al. (2025), *Data-driven insights into (E-)bike-sharing*, Transportation. [DOI](https://doi.org/10.1007/s11116-025-10661-2).

The dataset is licensed under CC BY-NC 4.0.

## Current Progress

- [x] Dataset extraction and city filtering
- [x] Station-level data quality analysis
- [x] Hourly time-series reconstruction
- [x] Shortage analysis and baseline evaluation
- [x] Exploratory Jupyter notebook
- [ ] Feature engineering and ML model training
- [ ] Model evaluation and comparison
- [ ] Interactive dashboard
