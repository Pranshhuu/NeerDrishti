const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

export function getFlowConcentrationImageUrl(): string {
  return `${API_BASE_URL}/api/v1/runoff/flow-concentration/image`;
}

export interface FlowConcentrationResponse {
  status: string;
  dataset_filename: string;
  width: number;
  height: number;
  class_0_cells: number;
  class_1_cells: number;
  class_2_cells: number;
  class_3_cells: number;
  p95_accumulation_cells: number;
  p99_accumulation_cells: number;
  terrain_source: string;
  boundary_source: string;
  interpretation: string;
  generated_at: string;
}

export class RunoffApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "RunoffApiError";
    this.status = status;
  }
}

export async function getFlowConcentration(): Promise<FlowConcentrationResponse> {
  let response: Response;

  try {
    response = await fetch(
      `${API_BASE_URL}/api/v1/runoff/flow-concentration`,
      {
        cache: "no-store",
      }
    );
  } catch {
    throw new RunoffApiError(
      "Unable to reach the NeerDrishti backend. Is the backend running?"
    );
  }

  if (!response.ok) {
    throw new RunoffApiError(
      `Flow concentration service returned an error (HTTP ${response.status}).`,
      response.status
    );
  }

  try {
    return (await response.json()) as FlowConcentrationResponse;
  } catch {
    throw new RunoffApiError(
      "Flow concentration service returned an unreadable response."
    );
  }
}

export interface WardRunoffAllocation {
  ward_id: number;
  ward_name: string;
  basin_count: number;
  contributing_area_m2: number;
  runoff_low_m3s: number;
  runoff_high_m3s: number;
}

export interface WardRunoffResponse {
  status: string;
  eligible_basin_count: number;
  ward_count: number;
  rainfall_intensity_mm_h: number;
  allocations: WardRunoffAllocation[];
  outside_bmc_runoff_low_m3s: number;
  outside_bmc_runoff_high_m3s: number;
  source: {
    rainfall_source: string;
    rainfall_scenario: string;
    terrain_source: string;
    landcover_source: string;
    coefficient_status: string;
  };
  generated_at: string;
}

export async function getWardRunoff(
  rainfallIntensityMmH = 25.3,
  rainfallSource = "ERA5-Land",
  rainfallScenario = "2024-06-27T07:00"
): Promise<WardRunoffResponse> {
  const params = new URLSearchParams({
    rainfall_intensity_mm_h: rainfallIntensityMmH.toString(),
    rainfall_source: rainfallSource,
    rainfall_scenario: rainfallScenario,
  });

  let response: Response;

  try {
    response = await fetch(
      `${API_BASE_URL}/api/v1/runoff/wards?${params.toString()}`,
      {
        cache: "no-store",
      }
    );
  } catch {
    throw new RunoffApiError(
      "Unable to reach the NeerDrishti runoff service."
    );
  }

  if (!response.ok) {
    throw new RunoffApiError(
      `Ward runoff service returned an error (HTTP ${response.status}).`,
      response.status
    );
  }

  try {
    return (await response.json()) as WardRunoffResponse;
  } catch {
    throw new RunoffApiError(
      "Ward runoff service returned an unreadable response."
    );
  }
}