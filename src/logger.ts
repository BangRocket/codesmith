/**
 * Logging utility for Codesmith.
 */

import { appendFileSync, existsSync, mkdirSync } from "fs";
import { join, dirname } from "path";

const LOG_DIR = process.env.CODESMITH_LOG_DIR || "./logs";
const LOG_FILE = join(LOG_DIR, "codesmith.log");

// Ensure log directory exists
if (!existsSync(LOG_DIR)) {
  mkdirSync(LOG_DIR, { recursive: true });
}

type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

function formatTimestamp(): string {
  return new Date().toISOString();
}

function formatMessage(level: LogLevel, component: string, message: string, data?: unknown): string {
  const timestamp = formatTimestamp();
  const dataStr = data !== undefined ? ` | ${JSON.stringify(data)}` : "";
  return `[${timestamp}] [${level}] [${component}] ${message}${dataStr}`;
}

function writeLog(level: LogLevel, component: string, message: string, data?: unknown): void {
  const formatted = formatMessage(level, component, message, data);

  // Console output
  switch (level) {
    case "ERROR":
      console.error(formatted);
      break;
    case "WARN":
      console.warn(formatted);
      break;
    default:
      console.log(formatted);
  }

  // File output
  try {
    appendFileSync(LOG_FILE, formatted + "\n");
  } catch (err) {
    console.error("Failed to write to log file:", err);
  }
}

export function createLogger(component: string) {
  return {
    debug: (message: string, data?: unknown) => writeLog("DEBUG", component, message, data),
    info: (message: string, data?: unknown) => writeLog("INFO", component, message, data),
    warn: (message: string, data?: unknown) => writeLog("WARN", component, message, data),
    error: (message: string, data?: unknown) => writeLog("ERROR", component, message, data),
  };
}

// Pre-created loggers for main components
export const botLogger = createLogger("Bot");
export const sessionLogger = createLogger("Session");
export const claudeLogger = createLogger("Claude");
export const gitLogger = createLogger("Git");
