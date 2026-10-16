export interface SurfaceLocation {
  latitude: number;
  longitude: number;
}
export interface SurfaceFact {
  label: string;
  value: string;
}
export interface SurfaceDataset {
  categories: string[];
  series: SurfaceSeries[];
}
export interface SurfaceSeries {
  id: string;
  label: string;
  unit?: string;
  values: number[];
}

export interface SurfaceImage {
  path: string;
  alt: string;
  source?: string;
}
