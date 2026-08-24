import geopandas as gpd
from shapely.geometry import Point

SOURCE_CRS = "EPSG:4326"
TARGET_CRS = "EPSG:5179"


def reproject_points(df, lon_col="lon", lat_col="lat", source_crs=SOURCE_CRS, target_crs=TARGET_CRS):
    geometry = [Point(xy) for xy in zip(df[lon_col], df[lat_col])]
    gdf = gpd.GeoDataFrame(df.copy(), geometry=geometry, crs=source_crs)
    return gdf.to_crs(target_crs)


def reproject(gdf, target_crs=TARGET_CRS):
    return gdf.to_crs(target_crs)
