export class ApiClient {
  constructor(private readonly baseUrl: string) {}

  async health(): Promise<unknown> {
    const response = await fetch(`${this.baseUrl}/api/v1/health`);
    if (!response.ok) throw new Error(`Health check failed: ${response.status}`);
    return response.json();
  }
}
