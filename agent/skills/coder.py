# agent/skills/coder.py

import os
import git
from github import Github
import subprocess
import llm.manager

# --- Utility Functions ---

def extract_github_repo(main_repo_url):
    import re
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)(?:\.git)?", main_repo_url.strip())
    if m:
        return m.group(1)
    raise ValueError("Could not extract GitHub repo from URL")

def lint_and_format_file(file_path):
    if not file_path.endswith(".py"):
        return "No linting applied (not a Python file)."
    try:
        result = subprocess.run(
            ["black", "--diff", "--check", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        if result.returncode == 0:
            out = "✔️ Black: No formatting needed."
        else:
            subprocess.run(["black", file_path])
            out = "❗ Black: Reformatted file.\n" + result.stdout
        return out
    except Exception as e:
        return f"Linting failed: {e}"

def get_code_context(files_to_edit, repo_path, max_lines=40):
    context = ""
    for filename in files_to_edit:
        file_path = os.path.join(repo_path, filename)
        try:
            with open(file_path) as f:
                lines = f.readlines()
            snippet = "".join(lines[:max_lines])
            context += f"\n---\nFilename: {filename}\n{snippet}\n"
        except Exception as e:
            context += f"\n---\nFilename: {filename}\n[Could not read file: {e}]\n"
    return context

def enhance_and_lint_files(goal, files_to_edit, repo_path, code_context, coding_model):
    """
    Enhance, optimize, and lint files. 
    Returns: changes, lint_results
    """
    changes = {}
    lint_results = {}
    for filename in files_to_edit:
        file_path = os.path.join(repo_path, filename)
        with open(file_path) as f:
            old_code = f.read()
        prompt = (
            f"You are an expert developer. {goal}\n"
            f"Here is the current code of {filename}:\n{old_code}\n"
            f"Here is additional project context from other files:\n{code_context}\n"
            "Please provide clear enhancements and optimizations to the code of this file. "
            "Only make changes that are useful, do not break the code, and add helpful comments where appropriate. "
            "Do not introduce new features unless they directly support the stated goal."
        )
        new_code = llm.manager.ask_llm(prompt, model_name=coding_model, max_tokens=2048)
        with open(file_path, "w") as f:
            f.write(new_code)
        changes[filename] = new_code
        lint_results[filename] = lint_and_format_file(file_path)
    return changes, lint_results

def commit_and_push(repo, branch_name, goal):
    repo.git.add(all=True)
    repo.git.commit(m=f"AI: {goal}")
    origin = repo.remote(name="origin")
    origin.push(refspec=f"{branch_name}:{branch_name}")

def summarize_change_for_pr(goal, changes, model_name):
    files_changed = ", ".join(changes.keys())
    prompt = (
        f"Summarize the following code enhancement goal and the files changed:\n"
        f"Goal: {goal}\n"
        f"Files changed: {files_changed}\n"
        f"Provide a concise PR title and a detailed PR body in Markdown. "
        f"Title should be a single line. Body should explain what was improved or fixed and why."
    )
    result = llm.manager.ask_llm(prompt, model_name=model_name, max_tokens=300)
    if "\n" in result:
        title, body = result.split("\n", 1)
    else:
        title, body = result, ""
    return title.strip(), body.strip()

def create_or_update_pr(gh_repo, branch_name, target_base, pr_title, pr_body, main_reviewer, files_to_edit, lint_results):
    pulls = gh_repo.get_pulls(state='open', head=f"{gh_repo.owner.login}:{branch_name}")
    pr = None
    if pulls.totalCount == 0:
        pr = gh_repo.create_pull(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=target_base
        )
        pr.add_to_labels("AI-Generated")
        if main_reviewer:
            try:
                pr.add_to_assignees(main_reviewer)
            except Exception as e:
                print(f"Warning: Could not assign reviewer: {e}")
    else:
        pr = pulls[0]
    pr_url = pr.html_url

    # Optionally: comment linting results
    lint_comment = "### 🧹 Linting Results\n\n" + "\n".join(
        f"**{fname}:** {result}" for fname, result in lint_results.items()
    )
    pr.create_issue_comment(lint_comment)
    return pr, pr_url

def ai_project_idea_suggestions(goal, files_to_edit, repo_path, model_name):
    prompt = (
        "As an expert developer and project architect, review the following project files "
        f"({', '.join(files_to_edit)}) and the recent code enhancement goal: {goal}\n"
        "Suggest up to 3 significant and genuinely helpful project ideas, refactors, or new features "
        "that could meaningfully improve the codebase or its usefulness. "
        "For each idea, give a short summary and explain why it would be valuable. "
        "Output as a markdown list. Avoid duplicating what's already in the project."
    )
    return llm.manager.ask_llm(prompt, model_name=model_name, max_tokens=400)

def comment_ai_suggestions(pr, suggestions_md):
    suggestion_comment = (
        "### 🤖 AI Project Suggestions\n\n"
        "Here are some ideas for further improving this project:\n\n"
        f"{suggestions_md}\n\n"
        "*Generated by CodeLlama AI.*"
    )
    pr.create_issue_comment(suggestion_comment)

# --- Main Orchestration Function ---

def apply_code_change(goal, files_to_edit, repo_path, branch_name):
    # --- Load configs ---
    GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
    MAIN_REPO = os.getenv("MAIN_REPO")
    CODING_MODEL_NAME = get_coding_model_name()
    GITHUB_REPO = extract_github_repo(MAIN_REPO)
    MAIN_REVIEWER = os.getenv("MAIN_REVIEWER")
    target_base = "main"

    # --- Git setup ---
    repo = git.Repo(repo_path)
    if branch_name in repo.heads:
        new_branch = repo.heads[branch_name]
    else:
        new_branch = repo.create_head(branch_name)
    new_branch.checkout()
    origin = repo.remote(name="origin")
    remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
    origin.set_url(remote_url)

    # --- Code context & enhancement ---
    code_context = get_code_context(files_to_edit, repo_path)
    changes, lint_results = enhance_and_lint_files(goal, files_to_edit, repo_path, code_context, CODING_MODEL_NAME)

    # --- Git commit & push ---
    commit_and_push(repo, branch_name, goal)

    # --- PR creation & enrichment ---
    g = Github(GITHUB_TOKEN)
    gh_repo = g.get_repo(GITHUB_REPO)
    pr_title, pr_body_llm = summarize_change_for_pr(goal, changes, CODING_MODEL_NAME)
    pr_body = (
        pr_body_llm +
        f"\n\nFiles changed: {', '.join(files_to_edit)}\n"
        "This PR was generated by the AI assistant. Please review before merging.\n"
    )
    pr, pr_url = create_or_update_pr(
        gh_repo, branch_name, target_base, pr_title, pr_body, MAIN_REVIEWER, files_to_edit, lint_results
    )

    # --- AI suggestions as PR comment ---
    suggestions_md = ai_project_idea_suggestions(goal, files_to_edit, repo_path, CODING_MODEL_NAME)
    comment_ai_suggestions(pr, suggestions_md)

    return branch_name, changes, pr_url


def get_coding_model_name():
    coding_model = os.getenv("CODING_MODEL_NAME")
    llm_models = llm.manager.AVAILABLE_MODELS

    # Case 1: Model is set and valid
    if coding_model and coding_model in llm_models:
        return coding_model

    # Case 2: Model is set but not available
    if coding_model and coding_model not in llm_models:
        print(f"Warning: CODING_MODEL_NAME '{coding_model}' is not found in LLM_MODELS ({llm_models}).")
        # Prompt user for a model selection if running interactively
        if llm_models:
            print("Available models:")
            for idx, model in enumerate(llm_models, start=1):
                print(f"{idx}. {model}")
            try:
                choice = input("Select a model by number (or press Enter to abort): ").strip()
                if choice and choice.isdigit() and 1 <= int(choice) <= len(llm_models):
                    selected = llm_models[int(choice)-1]
                    print(f"Using model: {selected}")
                    return selected
                else:
                    print("Aborted by user.")
                    exit(1)
            except EOFError:
                print("No input available. Aborting.")
                exit(1)
        else:
            print("Error: No models are available in LLM_MODELS. Aborting.")
            exit(1)

    # Case 3: No coding model is set, but some models are available
    if not coding_model and llm_models:
        print(f"Warning: CODING_MODEL_NAME is not set. Available models: {llm_models}")
        try:
            choice = input("Select a model by number (or press Enter to abort): ").strip()
            if choice and choice.isdigit() and 1 <= int(choice) <= len(llm_models):
                selected = llm_models[int(choice)-1]
                print(f"Using model: {selected}")
                return selected
            else:
                print("Aborted by user.")
                exit(1)
        except EOFError:
            print("No input available. Aborting.")
            exit(1)

    # Case 4: No models at all
    print("Error: No models defined in .env (LLM_MODELS is empty). Aborting.")
    exit(1)