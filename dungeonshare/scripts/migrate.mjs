import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import pg from "pg";

const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  throw new Error("DATABASE_URL is required. Load it before running this command.");
}

const migrationsDir = path.join(process.cwd(), "db", "migrations");
const files = (await readdir(migrationsDir))
  .filter((file) => file.endsWith(".sql"))
  .sort();
const client = new pg.Client({ connectionString });

await client.connect();
try {
  for (const file of files) {
    const sql = await readFile(path.join(migrationsDir, file), "utf8");
    await client.query("begin");
    try {
      await client.query(sql);
      await client.query("commit");
      console.log(`Applied ${file}`);
    } catch (error) {
      await client.query("rollback");
      throw error;
    }
  }
} finally {
  await client.end();
}
