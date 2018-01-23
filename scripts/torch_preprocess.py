from lib.models.torch.preprocess import prepare_data
from sklearn.externals import joblib
import xarray as xr
from lib.data import inputs_and_forcings

inputs, forcings = inputs_and_forcings(snakemake.input)
output_data = prepare_data(inputs, forcings)
joblib.dump(output_data, snakemake.output[0])


o = snakemake.output
inputs.to_netcdf(o.inputs)
forcings.to_netcdf(o.forcings)
