import numpy as np
import pandas as pd

# Hard coded for now, interface it and make some functions later
unprocessed_df = pd.read_csv("../data/raw/DA-EnergyPrices-NO1.csv")
processed_df = unprocessed_df[['MTU (CET/CEST)', 'Day-ahead Price (EUR/MWh)']]

export_csv = processed_df.to_csv(r"../data/processed/DA-EnergyPrices-NO1-processed.csv", index=None, header=True)