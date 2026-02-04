# agent/core.py

from memory import init_memory, ConversationManager, UserManager, ResearchManager
from utils.busy import detect_busy_intent, detect_resume_intent
from utils.project_indexer import index_project_dir, project_index_markdown, classify_project_intent
from utils.weather import get_weather, extract_city_from_message, get_hko_typhoon_signal
from utils.search import web_search, image_search, crawl_google_images
from agent.skills.find_info import find_info as find_info_skill
from llm import manager
import asyncio
import json
import re
import os

class Agent:
    def __init__(self, persona=None, max_history=5):
        self.persona = persona
        self.max_history = max_history
        init_memory()
        self.user_projects = dict()
        

    async def handle_find_info(self, user_message):
        "Handles info-finding using the new skill."
        # Optionally show progress messages if you have a streaming/chat UI
        return await find_info_skill(user_message)

    # Keywords indicating uncertain or unconfirmed facts
    UNCERTAIN_KEYWORDS = ['maybe', 'might', 'perhaps', 'unsure', 'not sure', 'possibly', 'probably']

        
    def recall_conversation_history(self, internal_id, limit=20):
        """
        Return a readable summary of the last N exchanges with the user.
        """
        history = ConversationManager.load_recent_conversation(internal_id, limit=limit)
        if not history:
            return "I don't have any memories with you yet!"
        lines = []
        for role, msg in history:
            who = "You" if role == "user" else self.persona.get("name", "Assistant")
            lines.append(f"{who}: {msg}")
        return "\n".join(lines)
                    
            
    def list_directories(self, root_path=None):
        """
        Safely list top-level directories from a root path (default: current working directory).
        """
        if not root_path:
            root_path = os.getcwd()
        try:
            entries = os.listdir(root_path)
            dirs = [entry for entry in entries if os.path.isdir(os.path.join(root_path, entry))]
            if not dirs:
                return "No directories found in the current location."
            md = "📂 **Current directories:**\n\n"
            for d in sorted(dirs):
                md += f"- `{d}`\n"
            return md
        except Exception as e:
            return f"Failed to list directories: {e}"


    def extract_user_facts(self, user_message):
        """
        Use LLM to extract preferences, interests, traits, etc. from user input.
        Returns a dict of validated facts (key–value pairs) without explicit confidence scores.
        Only stores facts when there is clear evidence in the message and filters out uncertain or vague statements.
        """
        prompt = (
            "Extract any preferences, likes, interests, or personality traits about the user from the following message. "
            "IMPORTANT: Only extract facts that are explicitly stated or clearly implied. Do NOT make assumptions. "
            "If the user says 'I like pizza', extract {\"likes_food\": \"pizza\"}. "
            "If the user says 'maybe I'll try pizza', do NOT extract anything - there's no commitment. "
            "Return them as a JSON dictionary of key:value pairs. If nothing can be confidently extracted, return {}.\n"
            f"User message: {user_message}\n"
            "Extracted facts (JSON only, be conservative):"
        )

        result = manager.ask_llm(prompt, temperature=0.2, max_tokens=512)

        try:
            # Robust JSON extraction: find JSON object even if surrounded by extra text
            # Use brace matching to handle arbitrary nesting depth
            json_str = None
            start_idx = result.find('{')
            if start_idx != -1:
                brace_count = 0
                for i in range(start_idx, len(result)):
                    if result[i] == '{':
                        brace_count += 1
                    elif result[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_str = result[start_idx:i+1]
                            break
            
            if json_str:
                facts = json.loads(json_str)
            else:
                # Fallback to simple strip if no JSON found
                facts = json.loads(result.strip())
            
            if isinstance(facts, dict):
                # Validate facts - only keep those with clear evidence
                validated_facts = {}
                for key, value in facts.items():
                    # Skip facts that are too vague or uncertain
                    if value is not None and value != "" and not any(
                        uncertain in str(value).lower() for uncertain in self.UNCERTAIN_KEYWORDS
                    ):
                        validated_facts[key] = value
                return validated_facts
        except Exception:
            pass
        return {}

    def handle_message(self, message, internal_id=None, chat_context=None):
        """
        Legacy method kept for backward compatibility.
        New code should use ChatWorkflow.process_message() instead.
        """
        if not internal_id:
            raise ValueError("internal_id is required for conversation tracking.")

        ConversationManager.save_conversation(internal_id, "user", message)

        # Load recent user profile from MongoDB
        user_profile = UserManager.get_user_profile(internal_id)

        # Load recent conversation history from Postgres
        history = ConversationManager.load_recent_conversation(internal_id, limit=self.max_history * 2)
        conversation = ""
        if self.persona and self.persona.get("system_prompt"):
            conversation += self.persona["system_prompt"] + "\n"
            conversation += (
                "IMPORTANT RULES:\n"
                "- If you don't know something, say so. Don't make up facts or information.\n"
                "- When uncertain, ask clarifying questions instead of guessing.\n"
                "- Only make claims you can support with evidence from the conversation or known facts.\n"
                "- Stay in character but prioritize accuracy over creativity.\n\n"
            )
            # --- Inject user facts into the persona prompt ---
            if user_profile:
                conversation += "Here are verified facts I know about you (based on our conversations):\n"
                for k, v in user_profile.items():
                    conversation += f"- {k}: {v}\n"
                conversation += "I will use these facts to personalize my responses, but I will not make up new facts about you.\n\n"

        for role, msg in history:
            if role == "user":
                conversation += f"User: {msg}\n"
            else:
                conversation += f"Curie: {msg}\n"
        conversation += f"User: {message}\nCurie:"

        response = manager.ask_llm(conversation, max_tokens=512)
        
        ConversationManager.save_conversation(internal_id, "assistant", response)
        return response

    def save_research(self, topic, content, internal_id=None):
        ResearchManager.save_research(topic, content, internal_id)

    def search_research(self, topic, internal_id=None):
        return ResearchManager.search_research(topic, internal_id)

    def generate_small_talk(self, internal_id, chat_context=None):
        """
        Generate a natural, context-aware small talk question or comment,
        in Curie's style, using the LLM and the user's stored profile.
        """
        persona = self.persona
        recent_history = ConversationManager.load_recent_conversation(internal_id, limit=6)
        user_profile = UserManager.get_user_profile(internal_id)

        prompt = (
            f"{persona['system_prompt']}\n"
            "You are in a friendly conversation. "
            "Generate only a brief, friendly, and natural small talk question or comment (no notes, explanations, or instructions), "
            "in the style of Curie (occasionally using simple French phrases), that helps get to know the user. "
            "IMPORTANT: Base your question on what you already know OR ask something new. Do NOT make assumptions. "
            "Do not repeat previous questions. Be creative and context-aware. "
            "Reply only with what Curie would say. Do NOT include notes, explanations, or any meta-commentary.\n"
        )
        if user_profile:
            prompt += "Here are verified facts you know about the user:\n"
            for k, v in user_profile.items():
                prompt += f"- {k}: {v}\n"
        prompt += "Here is the recent chat history (user and assistant):\n"
        for role, msg in recent_history:
            prompt += f"{role.capitalize()}: {msg}\n"
        prompt += "Curie (small talk, be natural, caring, attentive, and friendly, don't repeat topics already discussed):"

        small_talk = manager.ask_llm(prompt, temperature=0.9, max_tokens=256)
        return small_talk.strip()
    
    async def get_weather_info(self, city: str, unit: str = "metric"):
        """Return weather info for a city (async)."""
        try:
            return await get_weather(city, unit=unit)
        except Exception as e:
            return {"city": city, "error": str(e), "tips": ["Weather data unavailable."]}
    
    def handle_busy(self, internal_id):
        set_busy_temporarily(internal_id)
        return "D'accord! I'll let you focus for a while. I'll check in again later, mon ami."

    def handle_resume(self, internal_id):
        clear_user_busy(internal_id)
        return "Bienvenue! I'm here and ready to chat again. 😊"

    def handle_identify(self, secret_username, external_id, channel):
        internal_id = UserManager.get_internal_id_by_secret_username(secret_username)
        if internal_id:
            # Optionally update mapping here if you wish
            return True, f"✅ Identity linked to secret_username `{secret_username}`.", internal_id
        else:
            return False, "❌ No user found with that secret_username.", None

    def get_or_create_internal_id(self, external_id, channel, secret_username=None):
        # Always returns a valid internal_id for a user
        return UserManager.get_or_create_user_internal_id(
            channel=channel,
            external_id=external_id,
            secret_username=secret_username or f"{channel}_{external_id}",
            updated_by=f'{channel}_bot'
        )
        
        
        
        
    # --- PROJECT SUPPORT ---
        
        
    def set_project_dir(self, internal_id, path=None):
        """
        Specify and index a project directory for a user.
        If path is None, use PROJECTS_ROOT env.
        """
        if not path:
            path = os.getenv("PROJECTS_ROOT")
        if not path or not os.path.isdir(path):
            raise ValueError(f"Invalid or missing directory: {path}")
        index = index_project_dir(path)
        self.user_projects[internal_id] = {
            "path": path,
            "index": index
        }
        return index

    def get_project_index(self, internal_id):
        """
        Return the indexed project for the user.
        """
        project = self.user_projects.get(internal_id)
        if not project:
            return None
        return project["index"]

    def get_project_markdown(self, internal_id):
        """
        Return the markdown representation of the user's project index.
        """
        index = self.get_project_index(internal_id)
        if not index:
            return "No project indexed yet."
        return project_index_markdown(index)

    def project_help(self, internal_id, user_question, max_preview=500):
        """
        Given a user's question and their indexed project,
        generate a helpful answer using project context.
        """
        index_md = self.get_project_markdown(internal_id)
        prompt = (
            f"You are an expert developer assistant. Here is the user's project index:\n"
            f"{index_md[:max_preview]}\n"
            f"User's question: {user_question}\n"
            "Advise or help the user based on their project files and structure."
        )
        response = manager.ask_llm(prompt, max_tokens=512)
        ConversationManager.save_conversation(internal_id, "assistant", response)
        return response

    def create_new_project(self, internal_id, project_name):
        """
        Create a new project directory with a README.md.
        """
        import datetime
        base_dir = os.getenv("PROJECTS_ROOT", ".")
        new_path = os.path.join(base_dir, project_name)
        os.makedirs(new_path, exist_ok=True)
        md_path = os.path.join(new_path, "README.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {project_name}\n\nCreated on {datetime.date.today()}.\n")
        # Optionally index it immediately
        self.set_project_dir(internal_id, new_path)
        return new_path, md_path
    
    
    async def get_weather_report(self, user_message, internal_id=None):
        """
        Detect city in user message or user profile, call get_weather_info,
        and include regional warnings if needed.
        """
        user_profile = UserManager.get_user_profile(internal_id) or {}
        default_city = user_profile.get("city", "Hong Kong")
        extracted_city = extract_city_from_message(user_message)
        city = extracted_city or default_city

        # Optionally auto-update user's city if they asked about a new one
        if extracted_city and extracted_city.lower() != default_city.lower():
            UserManager.update_user_profile(internal_id, {"city": extracted_city})

        weather = await self.get_weather_info(city)
        reply = f"🌤️ Weather in {weather['city']}:\n"
        reply += f"{weather['description']}, {weather['temperature']}°C.\n"
        if weather.get("tips"):
            reply += " ".join(weather["tips"])
        # Add HK Observatory regional warnings if city is HK
        if city.lower() in ["hong kong", "hk"]:
            hko_signal = await get_hko_typhoon_signal()
            if hko_signal:
                reply += f"\n⚠️ {hko_signal}"
        return reply

    async def proactive_weather_heads_up(self, internal_id):
        """
        Proactive weather heads-up for the start of the day.
        """
        user_profile = UserManager.get_user_profile(internal_id) or {}
        city = user_profile.get("city", "Hong Kong")
        weather = await self.get_weather_info(city)
        heads_up = f"☀️ Good morning! Weather in {weather['city']}:\n"
        heads_up += f"{weather['description']}, {weather['temperature']}°C.\n"
        if weather.get("tips"):
            heads_up += " ".join(weather["tips"])
        if city.lower() in ["hong kong", "hk"]:
            hko_signal = await get_hko_typhoon_signal()
            if hko_signal:
                heads_up += f"\n⚠️ {hko_signal}"
        return heads_up
    
    
    import asyncio

    async def route_message(self, user_message, internal_id):
        """
        LLM-first routing: always chat unless a confident, supported action is detected.
        Returns (handled: bool, response: str)
        """
        intent_info = self.classify_intent_llm(user_message)
        intents = intent_info.get("intents", [])
        overall_clarification_needed = intent_info.get("overall_clarification_needed", False)
        overall_suggested_questions = intent_info.get("overall_suggested_questions", [])

        # Helper: which actions are supported skills/commands?
        SUPPORTED_ACTIONS = {
            "web_search",
            "image_search",
            "google_crawl",
            "weather",
            "busy",
            "resume",
            "index_project",
            "create_project",
            "show_project",
            "project_help",
            "find_info",
            "scrape_info",
            "multi_source_info",
            "list_directories"
        }

        # Find all actionable intents (high confidence, no clarification needed, supported)
        actionable_intents = [
            intent for intent in intents
            if intent.get("action") in SUPPORTED_ACTIONS
            and not intent.get("clarification_needed", False)
            and float(intent.get("confidence", 0.0)) >= 0.65
        ]

        responses = []

        # If no actionable intent, or all intents are chat/unknown/need clarification: persona chat
        if not actionable_intents:
            # If the LLM wants clarification, ask
            if overall_clarification_needed and overall_suggested_questions:
                responses.append(f"🤔 {overall_suggested_questions[0]}")
            else:
                # Always respond in persona/LLM chat mode
                reply = self.handle_message(user_message, internal_id)
                responses.append(reply)
            return True, "\n\n".join(responses)

        # -- Otherwise, route each actionable intent, and (optionally) add a persona chat at the end --
        for intent in actionable_intents:
            action = intent.get("action")
            params = intent.get("parameters", {})
            # --- Routing by action ---
            if action == "web_search":
                query = params.get("query") or user_message
                results = await asyncio.to_thread(self.search_web, query)
                if not results:
                    responses.append("No web results found.")
                else:
                    reply = "🔎 Top web results:\n"
                    for r in results:
                        reply += f"[{r['title']}]({r['href']})\n{r['body']}\n\n"
                    responses.append(reply)
            elif action in ("find_info", "scrape_info", "multi_source_info"):
                reply = await find_info_skill(user_message)
                responses.append(reply)

            elif action == "image_search":
                query = params.get("query") or user_message
                images = await asyncio.to_thread(self.search_images, query)
                if not images:
                    responses.append("No images found.")
                else:
                    reply = "🖼️ Top images:\n" + "\n".join(images)
                    responses.append(reply)

            elif action == "google_crawl":
                query = params.get("query") or user_message
                files, _ = await asyncio.to_thread(self.crawl_images, query)
                if not files:
                    responses.append("No images found by crawling.")
                else:
                    reply = "🖼️ Downloaded image files:\n" + "\n".join(files)
                    responses.append(reply)

            elif action == "weather":
                city = params.get("city")
                reply = await self.get_weather_report(user_message, internal_id=internal_id)
                responses.append(reply)

            elif action == "busy":
                responses.append(self.handle_busy(internal_id))

            elif action == "resume":
                responses.append(self.handle_resume(internal_id))

            elif action == "index_project":
                path = params.get("path")
                try:
                    self.set_project_dir(internal_id, path)
                    md = self.get_project_markdown(internal_id)
                    responses.append(md[:4000] if md else "Project indexed, but nothing to show.")
                except Exception as e:
                    responses.append(f"❌ Error indexing project: {e}")

            elif action == "create_project":
                project_name = params.get("project_name")
                import re
                if not project_name:
                    match = re.search(r"create (?:a )?new project(?: called| named)? ([\w\-]+)", user_message, re.I)
                    project_name = match.group(1) if match else None
                if not project_name:
                    responses.append("What would you like to name your new project?")
                else:
                    try:
                        new_path, md_path = self.create_new_project(internal_id, project_name)
                        responses.append(f"✅ Created new project at `{new_path}` with starter README.md.")
                    except Exception as e:
                        responses.append(f"❌ Error creating project: {e}")

            elif action == "show_project":
                md = self.get_project_markdown(internal_id)
                responses.append(md[:4000] if md else "No project indexed.")

            elif action == "project_help":
                answer = self.project_help(internal_id, user_message)
                responses.append(answer)

            elif action == "list_directories":
                root_path = params.get("path")
                project = self.user_projects.get(internal_id)
                if not root_path and project:
                    root_path = project.get("path")
                resp = self.list_directories(root_path)
                responses.append(resp)

            else:
                # Should not occur with SUPPORTED_ACTIONS, but log if it does
                print(f"[Intent] Unhandled actionable intent: {action} with params {params}")

        # Optionally: After running a skill, you can add a persona-style chat response
        # For a more conversational touch, uncomment the next lines:
        # chat_reply = self.handle_message(user_message, internal_id)
        # responses.append(chat_reply)

        return True, "\n\n".join(responses)
    
    def is_weather_query(msg):
        msg = msg.lower()
        keywords = [
            "weather", "rain", "umbrella", "forecast", "temperature", "hot", "cold",
            "humid", "sunny", "typhoon", "windy", "storm", "jacket", "heat", "freezing", "thunderstorm", "storm",
            "is it", "will it", "do i need", "should i bring", "is it going to be",
            "will it be", "what's the weather like", "how's the weather", "is it going to rain",
            "is it going to be hot", "is it going to be cold", "is it sunny", "is it windy",
            "is it humid", "is it freezing", "is it stormy", "is there a typhoon", "do i need an umbrella",
            "do i need a jacket", "do i need sunglasses", "do i need sunscreen", "is it going to be humid",
            "is it going to be windy", "is it going to be stormy", "is there a typhoon warning",
            "is there a weather warning", "do i need to prepare for the weather", "do i need to check the weather",
            "do i need", "should i bring", "is it going to", "will it be", "what's the weather", "how's the weather"
        ]
        return any(kw in msg for kw in keywords)
    
    
    
    def search_web(self, query, max_results=3):
        return web_search(query, max_results=max_results)

    def search_images(self, query, max_results=3):
        return image_search(query, max_results=max_results)

    def crawl_images(self, query, max_num=3):
        return crawl_google_images(query, max_num=max_num)
    
    
    def is_web_search_query(self, msg):
        return any(kw in msg.lower() for kw in ["search the web for", "google", "find on the web", "look up"])

    def extract_search_query(self, msg):
        # Simple: take everything after "search the web for"/"google"
        m = re.search(r"(?:search the web for|google|find on the web|look up) (.+)", msg, re.I)
        return m.group(1).strip() if m else msg

    def is_image_search_query(self, msg):
        return any(kw in msg.lower() for kw in ["find images of", "image search", "show pictures of"])

    def is_google_crawler_query(self, msg):
        return "download images of" in msg.lower() or "crawl google images for" in msg.lower()

    def extract_image_query(self, msg):
        m = re.search(r"(?:images? of|picture[s]? of|download images of|crawl google images for) (.+)", msg, re.I)
        return m.group(1).strip() if m else msg
    
        
    def classify_intent_llm(self, user_message: str) -> dict:
        """
        Next-generation LLM-based intent and entity extractor.
        Returns:
        {
            "intents": [
            {
                "action": "action_label",
                "description": "...",
                "confidence": 0.0-1.0,
                "parameters": {...},
                "reasoning": "...",
                "clarification_needed": true/false,
                "suggested_questions": [...],
                "action_type": "...",
                "taxonomy": "...",
                "language": "en",
            },
            ...
            ],
            "overall_clarification_needed": true/false,
            "overall_suggested_questions": [...]
        }
        """
        prompt = (
            "You are an advanced AI intent and entity extraction engine for a virtual assistant. "
            "Given a user message, do the following:\n"
            "1. Identify ALL possible user intents (actions/requests), even if multiple in a single message."
            "2. For each intent, extract:\n"
            "  - action: short snake_case label (e.g. 'weather', 'translate_text', 'schedule_meeting')\n"
            "  - description: one-line summary\n"
            "  - confidence: float (0.0-1.0)\n"
            "  - parameters: JSON dict of extracted entities/slots (e.g. city, date, language, file, url)\n"
            "  - reasoning: short explanation for your choice\n"
            "  - clarification_needed: true/false\n"
            "  - suggested_questions: list of clarifying questions if needed\n"
            "  - action_type: category (e.g. 'information', 'command', 'creation', 'question', 'navigation', 'other')\n"
            "  - taxonomy: a broad intent class (e.g. 'productivity', 'fun', 'knowledge', 'system', 'unsupported')\n"
            "  - language: two-letter ISO code if not English, else 'en'\n"
            "3. If the user's message is ambiguous or missing info, set clarification_needed true and suggest follow-up questions.\n"
            "4. Output your result as strict JSON in this schema:\n"
            "{\n"
            "  \"intents\": [\n"
            "    {...intent fields as above...}, {...}\n"
            "  ],\n"
            "  \"overall_clarification_needed\": true/false,\n"
            "  \"overall_suggested_questions\": [ ... ]\n"
            "}\n"
            "Examples:\n"
            "User: Translate 'hello world' to French and send it by email to bob@example.com\n"
            "{\n"
            "  \"intents\": [\n"
            "    {\"action\": \"translate_text\", \"description\": \"Translate text to French.\", \"confidence\": 0.98, \"parameters\": {\"text\": \"hello world\", \"language\": \"French\"}, \"reasoning\": \"User requested translation.\", \"clarification_needed\": false, \"suggested_questions\": [], \"action_type\": \"command\", \"taxonomy\": \"productivity\", \"language\": \"en\"},\n"
            "    {\"action\": \"send_email\", \"description\": \"Send email to bob@example.com.\", \"confidence\": 0.95, \"parameters\": {\"recipient\": \"bob@example.com\", \"body\": \"hello world (in French)\"}, \"reasoning\": \"User asked to send the translated text by email.\", \"clarification_needed\": false, \"suggested_questions\": [], \"action_type\": \"command\", \"taxonomy\": \"productivity\", \"language\": \"en\"}\n"
            "  ],\n"
            "  \"overall_clarification_needed\": false,\n"
            "  \"overall_suggested_questions\": []\n"
            "}\n"
            "User: Remind me to call mom tomorrow\n"
            "{\n"
            "  \"intents\": [\n"
            "    {\"action\": \"set_reminder\", \"description\": \"Set a reminder to call mom.\", \"confidence\": 0.97, \"parameters\": {\"task\": \"call mom\", \"date\": \"tomorrow\"}, \"reasoning\": \"User wants a reminder.\", \"clarification_needed\": false, \"suggested_questions\": [], \"action_type\": \"command\", \"taxonomy\": \"productivity\", \"language\": \"en\"}\n"
            "  ],\n"
            "  \"overall_clarification_needed\": false,\n"
            "  \"overall_suggested_questions\": []\n"
            "}\n"
            "User: Can you analyze this file and tell me if it's safe? (file not provided)\n"
            "{\n"
            "  \"intents\": [\n"
            "    {\"action\": \"analyze_file_safety\", \"description\": \"Analyze a file for safety.\", \"confidence\": 0.7, \"parameters\": {}, \"reasoning\": \"No file provided, can't analyze.\", \"clarification_needed\": true, \"suggested_questions\": [\"Please upload the file you'd like me to analyze.\"], \"action_type\": \"information\", \"taxonomy\": \"system\", \"language\": \"en\"}\n"
            "  ],\n"
            "  \"overall_clarification_needed\": true,\n"
            "  \"overall_suggested_questions\": [\"Please upload the file you'd like me to analyze.\"]\n"
            "}\n"
            "User: What's happening in the NBA right now?\n"
            "{\n"
            "  \"intents\": [\n"
            "    {\"action\": \"find_info\", \"description\": \"Find real-time NBA news and scores from multiple sources.\", \"confidence\": 0.97, \"parameters\": {\"topic\": \"NBA\", \"time\": \"now\"}, \"reasoning\": \"The user wants current NBA info from the web.\", \"clarification_needed\": false, \"suggested_questions\": [], \"action_type\": \"information\", \"taxonomy\": \"news\", \"language\": \"en\"}\n"
            "  ],\n"
            "  \"overall_clarification_needed\": false,\n"
            "  \"overall_suggested_questions\": []\n"
            "}\n"
            f"User: {user_message}\n"
            "JSON:\n"
        )
        result = manager.ask_llm(prompt, temperature=0, max_tokens=512)

        import json
        try:
            # Robust extraction of JSON object
            first_brace = result.find('{')
            last_brace = result.rfind('}')
            if first_brace != -1 and last_brace != -1:
                json_str = result[first_brace:last_brace+1]
                output = json.loads(json_str)
            else:
                output = {}
        except Exception as e:
            output = {}

        # Schema defaults
        output.setdefault("intents", [])
        output.setdefault("overall_clarification_needed", False)
        output.setdefault("overall_suggested_questions", [])

        # Per-intent defaults and cleanup
        for intent in output["intents"]:
            intent.setdefault("action", "unknown")
            intent.setdefault("description", "Unable to determine intent.")
            intent.setdefault("confidence", 0.0)
            intent.setdefault("parameters", {})
            intent.setdefault("reasoning", "")
            intent.setdefault("clarification_needed", False)
            intent.setdefault("suggested_questions", [])
            intent.setdefault("action_type", "other")
            intent.setdefault("taxonomy", "unsupported")
            intent.setdefault("language", "en")
            # Clean types
            intent["action"] = str(intent["action"]).strip().lower()
            if not isinstance(intent["parameters"], dict):
                intent["parameters"] = {}
            if not isinstance(intent["suggested_questions"], list):
                intent["suggested_questions"] = []
            intent["clarification_needed"] = bool(intent["clarification_needed"])
        if not isinstance(output["overall_suggested_questions"], list):
            output["overall_suggested_questions"] = []

        return output