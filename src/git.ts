/**
 * Git operations for user workspaces.
 */

import { exec } from "child_process";
import { promisify } from "util";
import { existsSync, writeFileSync, readFileSync } from "fs";
import { join } from "path";
import { GITHUB_TOKEN, GITHUB_OWNER, getUserWorkspace } from "./config.js";

const execAsync = promisify(exec);

interface GitResult {
  success: boolean;
  message: string;
  url?: string;
}

/**
 * Run a git command in the user's workspace.
 */
async function runGit(workspace: string, args: string): Promise<{ stdout: string; stderr: string }> {
  return execAsync(`git ${args}`, {
    cwd: workspace,
    env: {
      ...process.env,
      GIT_TERMINAL_PROMPT: "0",
    },
  });
}

/**
 * Check if workspace is a git repo.
 */
export function isGitRepo(workspace: string): boolean {
  return existsSync(join(workspace, ".git"));
}

/**
 * Get the remote URL for a workspace.
 */
export async function getRemoteUrl(workspace: string): Promise<string | null> {
  if (!isGitRepo(workspace)) return null;

  try {
    const { stdout } = await runGit(workspace, "remote get-url origin");
    return stdout.trim();
  } catch {
    return null;
  }
}

/**
 * Get the GitHub file URL for a file in the workspace.
 */
export async function getGitHubFileUrl(workspace: string, filePath: string): Promise<string | null> {
  const remoteUrl = await getRemoteUrl(workspace);
  if (!remoteUrl) return null;

  // Parse GitHub URL from remote
  // Formats: https://github.com/owner/repo.git, git@github.com:owner/repo.git
  let owner: string;
  let repo: string;

  const httpsMatch = remoteUrl.match(/github\.com\/([^/]+)\/([^/.]+)/);
  const sshMatch = remoteUrl.match(/github\.com:([^/]+)\/([^/.]+)/);

  if (httpsMatch) {
    owner = httpsMatch[1];
    repo = httpsMatch[2];
  } else if (sshMatch) {
    owner = sshMatch[1];
    repo = sshMatch[2];
  } else {
    return null;
  }

  // Get current branch
  let branch = "main";
  try {
    const { stdout } = await runGit(workspace, "rev-parse --abbrev-ref HEAD");
    branch = stdout.trim();
  } catch {
    // Use default
  }

  return `https://github.com/${owner}/${repo}/blob/${branch}/${filePath}`;
}

/**
 * Initialize git in workspace.
 */
export async function gitInit(userId: string): Promise<GitResult> {
  const workspace = getUserWorkspace(userId);

  if (isGitRepo(workspace)) {
    return { success: true, message: "Git repository already initialized." };
  }

  try {
    await runGit(workspace, "init");
    await runGit(workspace, 'config user.email "codesmith@bot.local"');
    await runGit(workspace, 'config user.name "Codesmith"');

    // Create .gitignore
    const gitignorePath = join(workspace, ".gitignore");
    if (!existsSync(gitignorePath)) {
      writeFileSync(gitignorePath, ".claude/\nnode_modules/\n.env\n");
    }

    return { success: true, message: "Git repository initialized." };
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return { success: false, message: `Failed to initialize git: ${msg}` };
  }
}

/**
 * Set remote URL for workspace.
 */
export async function gitSetRemote(userId: string, url: string): Promise<GitResult> {
  const workspace = getUserWorkspace(userId);

  if (!isGitRepo(workspace)) {
    return { success: false, message: "Not a git repository. Run `/cc git init` first." };
  }

  try {
    // Check if origin exists
    const hasOrigin = await getRemoteUrl(workspace);

    if (hasOrigin) {
      await runGit(workspace, `remote set-url origin "${url}"`);
    } else {
      await runGit(workspace, `remote add origin "${url}"`);
    }

    return { success: true, message: `Remote set to: ${url}` };
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return { success: false, message: `Failed to set remote: ${msg}` };
  }
}

/**
 * Commit all changes and push.
 */
export async function gitPush(userId: string, commitMessage?: string): Promise<GitResult> {
  const workspace = getUserWorkspace(userId);

  if (!isGitRepo(workspace)) {
    return { success: false, message: "Not a git repository. Run `/cc git init` first." };
  }

  const remoteUrl = await getRemoteUrl(workspace);
  if (!remoteUrl) {
    return { success: false, message: "No remote configured. Run `/cc git remote <url>` first." };
  }

  try {
    // Stage all changes
    await runGit(workspace, "add -A");

    // Check if there are changes to commit
    try {
      await runGit(workspace, "diff --cached --quiet");
      // If no error, there are no changes
      return { success: true, message: "No changes to push." };
    } catch {
      // There are changes, continue
    }

    // Commit
    const message = commitMessage || `Update from Codesmith - ${new Date().toISOString()}`;
    await runGit(workspace, `commit -m "${message.replace(/"/g, '\\"')}"`);

    // Push (may need to set upstream)
    try {
      await runGit(workspace, "push");
    } catch {
      // Try setting upstream
      await runGit(workspace, "push -u origin HEAD");
    }

    return { success: true, message: "Changes pushed successfully.", url: remoteUrl };
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return { success: false, message: `Failed to push: ${msg}` };
  }
}

/**
 * Create a new repo on GitHub and set it as remote.
 */
export async function gitCreate(userId: string, repoName: string): Promise<GitResult> {
  if (!GITHUB_TOKEN || !GITHUB_OWNER) {
    return {
      success: false,
      message: "GitHub not configured. Ask the server admin to set GITHUB_TOKEN and GITHUB_OWNER.",
    };
  }

  const workspace = getUserWorkspace(userId);

  // Sanitize repo name
  const safeName = repoName.replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase();

  // Initialize git if needed
  if (!isGitRepo(workspace)) {
    const initResult = await gitInit(userId);
    if (!initResult.success) return initResult;
  }

  try {
    // Create repo via GitHub API
    const response = await fetch("https://api.github.com/user/repos", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({
        name: safeName,
        private: false,
        auto_init: false,
        description: `Created by Codesmith for Discord user ${userId}`,
      }),
    });

    if (!response.ok) {
      const error = (await response.json()) as { message?: string; errors?: Array<{ message?: string }> };
      if (response.status === 422 && error.errors?.[0]?.message?.includes("already exists")) {
        // Repo exists, just set remote
        const repoUrl = `https://github.com/${GITHUB_OWNER}/${safeName}`;
        await gitSetRemote(userId, repoUrl);
        return {
          success: true,
          message: `Repository already exists. Remote set to: ${repoUrl}`,
          url: repoUrl,
        };
      }
      return { success: false, message: `GitHub API error: ${error.message || response.statusText}` };
    }

    const repo = (await response.json()) as { html_url: string };
    const repoUrl = repo.html_url;

    // Set remote with token for push access
    const pushUrl = `https://${GITHUB_TOKEN}@github.com/${GITHUB_OWNER}/${safeName}.git`;
    await gitSetRemote(userId, pushUrl);

    // Initial commit and push
    await runGit(workspace, "add -A");
    try {
      await runGit(workspace, 'commit -m "Initial commit from Codesmith"');
    } catch {
      // May already have commits
    }

    try {
      await runGit(workspace, "branch -M main");
      await runGit(workspace, "push -u origin main");
    } catch (error) {
      // Push might fail if empty, that's ok
      console.error("Initial push error (may be ok):", error);
    }

    return {
      success: true,
      message: `Repository created: ${repoUrl}`,
      url: repoUrl,
    };
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return { success: false, message: `Failed to create repository: ${msg}` };
  }
}

/**
 * Store user's git remote config.
 */
export function getUserGitConfig(userId: string): { remote?: string } {
  const workspace = getUserWorkspace(userId);
  const configPath = join(workspace, ".codesmith-git.json");

  if (existsSync(configPath)) {
    try {
      return JSON.parse(readFileSync(configPath, "utf-8"));
    } catch {
      return {};
    }
  }
  return {};
}
