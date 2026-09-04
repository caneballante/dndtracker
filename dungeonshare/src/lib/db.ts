import { neon } from "@neondatabase/serverless";
import "server-only";

type SqlClient = ReturnType<typeof neon>;

let sqlClient: SqlClient | null = null;

export function hasDatabase(): boolean {
  return Boolean(process.env.DATABASE_URL);
}

export function getSql(): SqlClient {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not configured.");
  }

  if (!sqlClient) {
    sqlClient = neon(connectionString);
  }

  return sqlClient;
}
