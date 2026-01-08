/**
 * Claude Agent SDK wrapper.
 */

import { query } from "@anthropic-ai/claude-agent-sdk";
import type { TextChannel } from "discord.js";
import { DISCORD_MSG_LIMIT, ensureWorkspace, DEFAULT_MODEL } from "./config.js";
import { getAuthMethod } from "./auth.js";
import { AuthMethod, type StatusData, type UserSession } from "./types.js";
import { claudeLogger as log } from "./logger.js";

/**
 * Default status data.
 */
export function defaultStatus(): StatusData {
  return {
    model: DEFAULT_MODEL,
    inputTokens: 0,
    outputTokens: 0,
    cost: 0,
    contextPercent: 0,
  };
}

/**
 * Chunk text for Discord's message limit.
 */
export function chunkForDiscord(text: string, limit = DISCORD_MSG_LIMIT): string[] {
  if (text.length <= limit) {
    return text ? [text] : [];
  }

  const chunks: string[] = [];
  let remaining = text;

  while (remaining.length > 0) {
    if (remaining.length <= limit) {
      chunks.push(remaining);
      break;
    }

    const chunk = remaining.slice(0, limit);
    let splitPoint = limit;

    // Try to split at code block boundary
    const codeBlockEnd = chunk.lastIndexOf("```\n");
    if (codeBlockEnd > limit / 2) {
      splitPoint = codeBlockEnd + 4;
    } else {
      // Try paragraph break
      const paraBreak = chunk.lastIndexOf("\n\n");
      if (paraBreak > limit / 2) {
        splitPoint = paraBreak + 2;
      } else {
        // Try line break
        const lineBreak = chunk.lastIndexOf("\n");
        if (lineBreak > limit / 2) {
          splitPoint = lineBreak + 1;
        } else {
          // Try space
          const space = chunk.lastIndexOf(" ");
          if (space > limit / 2) {
            splitPoint = space + 1;
          }
        }
      }
    }

    let chunkText = remaining.slice(0, splitPoint);

    // Handle unclosed code blocks
    const codeOpens = (chunkText.match(/```/g) || []).length;
    if (codeOpens % 2 === 1) {
      chunkText = chunkText.trimEnd() + "\n```";
      remaining = "```\n" + remaining.slice(splitPoint);
    } else {
      remaining = remaining.slice(splitPoint);
    }

    chunks.push(chunkText);
  }

  return chunks;
}

/**
 * Send output to Discord, handling chunking.
 */
async function sendToDiscord(channel: TextChannel, content: string): Promise<void> {
  if (!content.trim()) return;

  const chunks = chunkForDiscord(content);
  log.debug(`Sending ${chunks.length} chunk(s) to Discord`, { contentLength: content.length });

  for (const chunk of chunks) {
    try {
      await channel.send(chunk);
    } catch (error) {
      log.error("Failed to send message to Discord", {
        error: error instanceof Error ? error.message : String(error),
      });
      break;
    }

    // Small delay between chunks to avoid rate limits
    if (chunks.length > 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
}

/**
 * Run a Claude Code query for a user session.
 */
export async function runClaudeQuery(
  session: UserSession,
  prompt: string,
  onStatusUpdate: (status: StatusData) => void
): Promise<void> {
  const authMethod = getAuthMethod(session.userId);
  log.info(`Starting query for user ${session.userId}`, {
    authMethod,
    promptLength: prompt.length,
    promptPreview: prompt.substring(0, 100),
  });

  if (authMethod === AuthMethod.NONE) {
    log.warn(`No auth configured for user ${session.userId}`);
    await sendToDiscord(
      session.channel,
      "No authentication configured. Use `/cc login` to authenticate with your Claude Max/Pro subscription, or ask the server admin to set `ANTHROPIC_API_KEY`."
    );
    return;
  }

  // Ensure workspace exists
  const workspace = ensureWorkspace(session.userId);
  log.debug(`Using workspace: ${workspace}`);

  let buffer = "";
  let lastSend = Date.now();
  const BUFFER_DELAY_MS = 500;
  const BUFFER_SIZE_THRESHOLD = 1500;
  let messageCount = 0;

  const flushBuffer = async () => {
    if (buffer.trim()) {
      await sendToDiscord(session.channel, buffer);
      buffer = "";
    }
    lastSend = Date.now();
  };

  try {
    // Send initial indicator
    await session.channel.sendTyping();

    log.info(`Calling Agent SDK query`, {
      userId: session.userId,
      model: session.status.model,
      workspace,
    });

    // Use the Agent SDK query function
    const result = query({
      prompt,
      options: {
        cwd: workspace,
        permissionMode: "bypassPermissions",
        model: session.status.model,
        allowedTools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        abortController: session.abortController,
      },
    });

    log.debug(`Query iterator created, starting to consume messages`);

    for await (const message of result) {
      messageCount++;
      session.lastActivity = new Date();

      // Handle different message types based on SDK structure
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const msg = message as any;

      log.debug(`Received message #${messageCount}`, {
        type: msg.type,
        subtype: msg.subtype,
        hasContent: !!msg.content,
        hasResult: !!msg.result,
        hasUsage: !!msg.usage,
      });

      // Update status from usage info if present
      if (msg.usage) {
        const newStatus = { ...session.status };
        if (msg.usage.input_tokens) {
          newStatus.inputTokens = msg.usage.input_tokens;
        }
        if (msg.usage.output_tokens) {
          newStatus.outputTokens = msg.usage.output_tokens;
        }
        if (msg.cost_usd !== undefined) {
          newStatus.cost = msg.cost_usd;
        }
        session.status = newStatus;
        onStatusUpdate(newStatus);
        log.debug(`Updated status`, newStatus);
      }

      // Handle text content
      if (msg.type === "text" && msg.content) {
        buffer += msg.content;
      } else if (msg.type === "assistant" && msg.message?.content) {
        // Assistant response with content blocks
        for (const block of msg.message.content) {
          if (block.type === "text" && block.text) {
            buffer += block.text;
          }
        }
      } else if ("result" in msg && msg.result) {
        // Final result
        log.info(`Query completed with result`, {
          userId: session.userId,
          resultLength: String(msg.result).length,
        });
        buffer += "\n" + msg.result;
      }

      // Flush buffer if needed
      const elapsed = Date.now() - lastSend;
      if (buffer.length > BUFFER_SIZE_THRESHOLD || elapsed > BUFFER_DELAY_MS) {
        await flushBuffer();
        await session.channel.sendTyping();
      }
    }

    // Flush remaining buffer
    await flushBuffer();

    log.info(`Query stream ended for user ${session.userId}`, {
      totalMessages: messageCount,
    });
  } catch (error) {
    await flushBuffer();

    const errorMessage = error instanceof Error ? error.message : String(error);
    const errorStack = error instanceof Error ? error.stack : undefined;

    log.error(`Query error for user ${session.userId}`, {
      error: errorMessage,
      stack: errorStack,
      messagesReceived: messageCount,
      aborted: session.abortController.signal.aborted,
    });

    if (session.abortController.signal.aborted) {
      await sendToDiscord(session.channel, "*Session cancelled.*");
      return;
    }

    // Handle specific error codes
    if (errorMessage.includes("rate limit") || errorMessage.includes("429")) {
      await sendToDiscord(
        session.channel,
        "Rate limited. Please wait a moment before trying again."
      );
    } else if (errorMessage.includes("401") || errorMessage.includes("unauthorized")) {
      await sendToDiscord(
        session.channel,
        "Authentication failed. Please re-authenticate with `/cc login`."
      );
    } else {
      await sendToDiscord(session.channel, `Error: ${errorMessage}`);
    }
  }
}

/**
 * Send a slash command to Claude Code.
 */
export async function sendSlashCommand(
  session: UserSession,
  command: string,
  onStatusUpdate: (status: StatusData) => void
): Promise<void> {
  log.info(`Sending slash command for user ${session.userId}`, { command });
  // Slash commands are just prompts
  await runClaudeQuery(session, command, onStatusUpdate);
}
