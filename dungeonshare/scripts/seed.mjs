import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import pg from "pg";

const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  throw new Error("DATABASE_URL is required. Load it before running this command.");
}

const client = new pg.Client({ connectionString });
await client.connect();
try {
  const sql = await readFile(path.join(process.cwd(), "db", "seed.sql"), "utf8");
  await client.query(sql);
  console.log("Seeded starter campaigns.");
} finally {
  await client.end();
}
