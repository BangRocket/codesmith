/**
 * Codesmith - Claude Code to Discord bridge.
 *
 * Main entry point.
 */

import { CodesmithBot } from "./bot.js";

const bot = new CodesmithBot();

// Handle graceful shutdown
process.on("SIGINT", async () => {
  console.log("\nReceived SIGINT, shutting down...");
  await bot.stop();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  console.log("\nReceived SIGTERM, shutting down...");
  await bot.stop();
  process.exit(0);
});

// Start the bot
console.log("Starting Codesmith bot...");
bot.start().catch((error) => {
  console.error("Failed to start bot:", error);
  process.exit(1);
});
