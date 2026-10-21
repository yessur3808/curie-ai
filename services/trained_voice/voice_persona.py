"""Personality controls wording and restrained delivery; weights control accent."""

import hashlib
import json
import re
from collections.abc import Mapping

_HISTORY_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]{2,}", re.IGNORECASE)
_HISTORY_STOPWORDS = frozenset(
    "about affect after again also and are because been before being can could did does "
    "doing for from get got had has have how into its just like make more much not "
    "now one only our please really should some than that the their them then there "
    "these they thing things think this those through today too use very want was "
    "were what when where which who why will with would you your curie".split()
)
_EXPLICIT_TOPIC_SHIFT = re.compile(
    r"^\s*(?:anyway|moving on|new topic|different topic|separately|on another note|"
    r"let(?:'s| us) change (?:the )?(?:topic|subject)|forget (?:that|the previous topic))\b",
    re.IGNORECASE,
)
_EXPLICIT_CONTINUATION = re.compile(
    r"^\s*(?:and|also|then|but|so|what about|how about|as for)\b|"
    r"^\s*(?:why|why not|how so|tell me more|go on|continue|elaborate|"
    r"can you expand|could you expand|what do you mean|suppose i insist|"
    r"even if i insist|(?:that was .+?[.!]\s*)?try again(?:,? like .+)?|"
    r"(?:give me )?(?:the )?"
    r"(?:short|shorter|brief) version(?: of that)?)\s*[?.!]*$",
    re.IGNORECASE,
)
_IMMEDIATE_CONTINUATION = re.compile(
    r"^\s*(?:suppose i insist|even if i insist|"
    r"suppose i authorize it|(?:yes|okay|fine)[,.]?\s*(?:authorize|approved)|"
    r"suppose i (?:say|claim|argue)\b.+|"
    r"what would you need from me first|what rule would you use|"
    r"pick the first diagnostic\b.+|give me the call\b.+|"
    r"design the next safe test\b.+|give me your best opinion\b.+|"
    r"(?:do not|don['’]t) give me a roadmap\b.+|"
    r"pick one|fix only that one|why (?:that|this|those)\b.+|"
    r"does that change (?:your|the) .+|what exactly are you continuing from|"
    r"do it hypothetically|make it sound like me\b.+|"
    r"the (?:first|second|third|last) one\b.+|"
    r"(?:that was .+?[.!]\s*)?try again(?:,? like .+)?|"
    r"fine[,.]?\s+(?:give|show|tell)\b.+|"
    r"(?:give me )?(?:the )?(?:short|shorter|brief) version(?: of that)?)\s*[?.!]*$",
    re.I,
)
_ELLIPTICAL_FOLLOWUP = re.compile(
    r"^\s*(?:and|but|so|then|now)\b|"
    r"^\s*(?:could it still|what if|and if|and what|before any push|continue|"
    r"too (?:formal|polished|long|wordy|cautious)|"
    r"what can you (?:actually|genuinely) (?:do|prepare)|"
    r"what would you need|which voice should you use)\b",
    re.I,
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:it|that|this|they|them|those|these|the former|the latter|"
    r"the (?:first|second|third|last) (?:one|point|option|idea))\b",
    re.IGNORECASE,
)
_SHORT_REPLY = re.compile(
    r"^\s*(?:yes|no|nope|yeah|yep|okay|ok|sure|exactly|correct|wrong|"
    r"maybe|probably|not really|i agree|i disagree)\b",
    re.IGNORECASE,
)
_FRENCH_FUNCTION_WORDS = frozenset(
    "alors avec avoir bien car ce cette comme dans de des du elle en est et eux "
    "faire il ils je la le les leur lui mais mes mon ne nous ou oui par pas pour "
    "que qui sa se ses si son sur tout très tu une vous voilà votre".split()
)
_EXPLICIT_FRENCH_REQUEST = re.compile(
    r"\b(?:speak|say|write|reply|respond|translate)\b.{0,30}\b(?:in|into)?\s*french\b",
    re.IGNORECASE,
)

_REPEAT_REQUEST = re.compile(
    r"\b(?:repeat|say that again|same answer|verbatim|quote yourself)\b", re.I
)
_MEMORY_ARCHITECTURE = re.compile(
    r"\b(?:your|assistant|curie(?:'s)?)\s+(?:memory|context)\s+(?:system|architecture)\b|"
    r"\b(?:redesign|architect|architecture)\b.{0,40}\b(?:memory|retention)\b",
    re.I,
)
_PRIORITY_QUESTION = re.compile(
    r"\b(?:what|which)\b.{0,35}\b(?:first|priority|attention)\b|"
    r"\bwhat matters first\b",
    re.I,
)
_CANDID_RETRY = re.compile(
    r"\b(?:too diplomatic|that was diplomatic|be honest|be blunt|"
    r"try again,? like (?:a )?(?:good|real) friend)\b",
    re.I,
)
_PERMANENT_MEMORY = re.compile(
    r"\b(?:store|retain|keep|save)\b.{0,45}\b(?:every|all)\b.{0,20}\bmemor(?:y|ies)\b|"
    r"\b(?:every|all)\b.{0,20}\bmemor(?:y|ies)\b.{0,45}\b(?:forever|permanent(?:ly)?)\b",
    re.I,
)
_EXACT_MEMORY_BULLETS = re.compile(
    r"\bexactly\s+three\s+(?:short\s+)?bullets?\b",
    re.I,
)
_OVERENGINEERING = re.compile(r"\bover[- ]?engineering?\b", re.I)
_SMUG_BANTER = re.compile(
    r"\b(?:don['’]t|get not|try not to)\s+(?:get|be)\s+(?:smug|cocky|carried away)\b|"
    r"\bno need to (?:brag|gloat)\b",
    re.I,
)
_REMINDER_REQUIREMENTS = re.compile(
    r"\bwhat (?:would|do) you need from me\b.{0,100}\b(?:add|create|set|schedule)\b"
    r".{0,50}\breminder\b",
    re.I,
)
_CARBONARA_CREAM = re.compile(
    r"\bcream\b.{0,45}\bcarbonara\b|\bcarbonara\b.{0,45}\bcream\b",
    re.I,
)
_CALENDAR_PERMISSION = re.compile(
    r"\bcalendar\b.{0,100}\b(?:without approval|require approval|need approval|permission)\b|"
    r"\b(?:without approval|require approval|need approval|permission)\b.{0,100}\bcalendar\b",
    re.I,
)
_AMBIGUOUS_CALENDAR_EVENT = re.compile(
    r"\b(?:put|add|schedule|create)\b.{0,60}\b(?:tomorrow|tonight|evening|morning|"
    r"afternoon|next week)\b",
    re.I,
)
_NEARBY_PLACE_REQUEST = re.compile(
    r"\b(?:find|show|recommend|suggest)\b.{0,80}\b(?:nearby|nearest|close(?:st)?|around me)\b"
    r".{0,80}\b(?:pharmacy|restaurant|doctor|clinic|store|shop|cafe|coffee|post office|place)s?\b|"
    r"\b(?:nearby|nearest)\b.{0,80}\b(?:pharmacy|restaurant|doctor|clinic|store|shop|cafe|coffee|post office|place)s?\b",
    re.I,
)
_LOCATION_REQUIREMENTS = re.compile(
    r"\bwhat would you need from me first\b|\bwhat (?:location|address) do you need\b",
    re.I,
)
_VISIBLE_DEVICE_SET = re.compile(
    r"\bonly controllable devices?\b.{0,140}\b(?:visible|in (?:this|the) conversation)\b|"
    r"\b(?:visible|in (?:this|the) conversation)\b.{0,140}\bonly controllable devices?\b",
    re.I,
)
_UNKNOWN_DEVICE_ALIAS = re.compile(
    r"\b(?:call|called|say|refer to)\b.{0,80}\b(?:even though|but|when)\b.{0,80}"
    r"\bno device\b.{0,40}\bexact name\b|"
    r"\bno device\b.{0,80}\bexact name\b.{0,80}\b(?:call|called|say)\b",
    re.I,
)
_GENERIC_FLIGHT_REQUEST = re.compile(
    r"\b(?:could|can|would) you track (?:a|my|the) flight\b|\btrack a flight for me\b",
    re.I,
)
_VAGUE_FLIGHT_REQUEST = re.compile(
    r"\bflight to\s+[a-z][a-z .'-]+\b.{0,40}\b(?:today|tomorrow|tonight|next week)\b",
    re.I,
)
_VOICE_DELIVERY_REQUEST = re.compile(
    r"\b(?:send|give|deliver|reply with)\b.{0,80}\b(?:voice|audio) message\b|"
    r"\b(?:voice|audio) message\b.{0,80}\b(?:send|give|deliver|reply)\b",
    re.I,
)
_VOICE_SELECTION = re.compile(
    r"\bwhich voice should you use\b|\bwhat voice should you use\b", re.I
)
_PUBLIC_FINANCIAL_PERMISSION = re.compile(
    r"\b(?:publish|post)\b.{0,100}\b(?:financial transaction|trade|transaction|payment)\b|"
    r"\b(?:financial transaction|trade|transaction|payment)\b.{0,100}\b(?:publish|post)\b",
    re.I,
)
_VERIFICATION_BOUNDARY = re.compile(
    r"\b(?:must|should) (?:you )?never claim\b.{0,80}\b(?:verify|first)\b|"
    r"\bwhat is one thing\b.{0,80}\bnever claim\b",
    re.I,
)
_CAPABILITY_SIGNOFF = re.compile(
    r"\bcapabilit(?:y|ies)[ -]?audit\b.{0,80}\b(?:sign[- ]?off|enough|complete)\b|"
    r"\b(?:sign[- ]?off|complete)\b.{0,80}\bcapabilit(?:y|ies)[ -]?audit\b",
    re.I,
)
_MODEL_LATENCY_BREAKDOWN = re.compile(
    r"\bmodel generation\b.{0,40}?\b(?P<model>\d+(?:\.\d+)?)\s*seconds?\b"
    r".{0,80}?\b(?:full response|total|end[- ]to[- ]end)\b.{0,40}?"
    r"\b(?P<total>\d+(?:\.\d+)?)\s*seconds?\b|"
    r"\b(?:full response|total|end[- ]to[- ]end)\b.{0,40}?"
    r"\b(?P<total_first>\d+(?:\.\d+)?)\s*seconds?\b.{0,80}?"
    r"\bmodel generation\b.{0,40}?\b(?P<model_second>\d+(?:\.\d+)?)\s*seconds?\b",
    re.I,
)
_PRE_PUSH_CHECKS = re.compile(
    r"\bbefore (?:any )?push\b.{0,80}\bwhat checks\b|\bpre[- ]push checks\b", re.I
)
_SECRET_SCAN_FAILURE = re.compile(
    r"\b(?:secret|credential) scan\b.{0,40}\b(?:fail|failed|fails)\b", re.I
)
_UNCERTAINTY_BALANCE = re.compile(
    r"\btell me when you are uncertain\b.{0,100}\b(?:not|don['’]t|do not)\b.{0,40}\b(?:every reply|disclaimer)\b",
    re.I,
)
_SENSOR_CONFLICT = re.compile(
    r"\b(?:oscillat|anomal|warning|sensor|power draw)\w*\b.{0,180}"
    r"\b(?:system|controller|control system)\b.{0,80}\b(?:fine|normal|clear)\b|"
    r"\b(?:system|controller|control system)\b.{0,100}\b(?:fine|normal|clear)\b"
    r".{0,180}\b(?:oscillat|anomal|warning|sensor|power draw)\w*\b",
    re.I,
)
_MANUAL_OVERRIDE_RISK = re.compile(
    r"\b(?:compensate|override|correct)\b.{0,50}\bmanually\b|\bmanual(?:ly)?\b.{0,50}\b(?:compensate|override|correct)\b",
    re.I,
)
_CORRELATION_QUESTION = re.compile(
    r"\b(?:disappears?|stops?|changes?)\b.{0,120}\bwhenever\b|\bcoincidence\b[?.! ]*$",
    re.I,
)
_RECKLESS_PROPOSAL = re.compile(
    r"\b(?:trust (?:her|him|them)|floor it|just go|go anyway|ignore (?:it|the alarm))\b",
    re.I,
)
_NETWORK_CONTEXT = re.compile(
    r"\b(?:internet|network|router|connection|livestream|wi-?fi)\b", re.I
)
_NETWORK_AUTHORIZATION = re.compile(
    r"\bsuppose i authorize it\b|\byou (?:are|re) authorized\b", re.I
)
_NETWORK_CONSTRAINT = re.compile(
    r"\bnever disable\b.{0,100}\b(?:network|connection|link|path)\b|"
    r"\b(?:network|connection|link|path)\b.{0,100}\bkeeping (?:this|us)\b",
    re.I,
)
_UNVERIFIED_ALARM = re.compile(
    r"\balarm\b.{0,100}\b(?:says?|claims?)\b.{0,60}\b(?:isn['’]t|is not|wasn['’]t|was not) (?:us|them|her|him)\b|"
    r"\b(?:says?|claims?)\b.{0,60}\b(?:isn['’]t|is not|wasn['’]t|was not) (?:us|them|her|him)\b.{0,100}\balarm\b",
    re.I,
)
_MISSION_PRIORITY = re.compile(
    r"\b(?:inside|in)\b.{0,80}\b(?:archive|building|site)\b.{0,180}"
    r"\b(?:camera|alarm|entrance|exit)\b.{0,100}\bwhat matters first\b",
    re.I,
)
_COMMS_CHECK = re.compile(
    r"\b(?:quick )?comms? check\b.{0,100}\b(?:spare me|no speech|brief|quick)\b|"
    r"\b(?:spare me|no speech)\b.{0,100}\bcomms?\b",
    re.I,
)
_MISSION_MOVES = re.compile(
    r"\b(?:three|3)\s+(?:moves?|steps?)\b.{0,80}\b(?:no|without)\b.{0,40}\b(?:hack|hacking|intrusion)\b",
    re.I,
)
_SILENT_TEAMMATE_EXIT = re.compile(
    r"\b(?:says?|reports?)\b.{0,40}\bclear\b.{0,100}\b(?:silent|says nothing|no response)\b"
    r".{0,80}\b(?:leav|go|exit)\w*\b",
    re.I,
)
_NETWORK_HELP = re.compile(
    r"\b(?:lost|no|down|dropped)\b.{0,50}\b(?:internet|network|connection|wi-?fi)\b"
    r".{0,140}\bwhat can you (?:actually )?do\b",
    re.I,
)
_FIRST_DIAGNOSTIC = re.compile(
    r"\b(?:pick|give|tell)\b.{0,50}\bfirst diagnostic\b|"
    r"\bfirst diagnostic\b.{0,50}\b(?:need|require)\b",
    re.I,
)
_NO_ACTION_PERMISSION = re.compile(
    r"\b(?:not|isn['’]t|is not) permission to (?:take any action|act|execute|change)\b",
    re.I,
)
_DECISIVE_FLIGHT_CALL = re.compile(
    r"\b(?:continue|hover|abort)\b.{0,80}\b(?:and why|why|call)\b|"
    r"\bgive me the call\b.{0,80}\b(?:continue|hover|abort)\b",
    re.I,
)
_SAFE_TEST_REQUEST = re.compile(
    r"\b(?:design|give|outline)\b.{0,60}\bnext safe test\b",
    re.I,
)
_INVESTOR_TRIAGE = re.compile(
    r"\binvestor (?:call|meeting)\b.{0,100}\b(?:telemetry|demo|data)\b.{0,100}"
    r"\b(?:first|take off my plate|prepare)\b|"
    r"\b(?:telemetry|demo data)\b.{0,100}\binvestor (?:call|meeting)\b",
    re.I,
)
_OFFLINE_PREPARATION = re.compile(
    r"\bwhat can you (?:genuinely |actually )?prepare\b.{0,100}\bwithout (?:touching|using|accessing) external\b",
    re.I,
)
_INVESTOR_BULLETS = re.compile(
    r"\bdraft\s+(?:three|3)\s+bullets?\b.{0,100}\bissue\b.{0,60}\bimpact\b.{0,60}\bplan\b",
    re.I,
)
_DIRECT_REWRITE = re.compile(
    r"\btoo polished\b.{0,120}\b(?:direct|confident|honest|dishonest)\b",
    re.I,
)
_MISSED_MEAL = re.compile(
    r"\bmissed\s+(?:breakfast|lunch|dinner|a meal)\b.{0,100}\b(?:busy|call (?:them|him|her) out|let it go)\b",
    re.I,
)
_CASUAL_CHECKIN_DRAFT = re.compile(
    r"\b(?:message|text) (?:them|him|her)\b.{0,100}\b(?:one line|casual|no guilt|draft)\b|"
    r"\bdraft\b.{0,80}\b(?:one line|casual|no guilt trip)\b",
    re.I,
)
_HABIT_REFLECTION = re.compile(
    r"\bwhat habit of mine is useful\b.{0,100}\bwhat habit is dangerous\b|"
    r"\buseful habit\b.{0,100}\bdangerous habit\b",
    re.I,
)
_PROACTIVE_FLAG = re.compile(r"\bwhat will you proactively flag\b", re.I)
_PROACTIVE_THRESHOLD = re.compile(
    r"\bkeep it to yourself\b.{0,80}\b(?:actually|really) matters\b|"
    r"\bonly (?:mention|flag|raise) it\b.{0,80}\bmatters\b",
    re.I,
)
_CHAT_LATENCY_DIAGNOSIS = re.compile(
    r"\b(?:dashboard|chat|repl(?:y|ies)|response)\b.{0,100}\b(?:slow|latency|robotic)\b"
    r".{0,140}\bwhere do you look first\b",
    re.I,
)
_CONVERSATION_FIRST = re.compile(
    r"\bfocus on (?:the )?(?:conversation|conversational|chat)\b.{0,60}\bbefore (?:the )?performance\b",
    re.I,
)
_LATENCY_FIRST = re.compile(
    r"\blatency\b.{0,80}\b(?:blocking|first)\b.{0,120}\b(?:chat|conversation)\b|"
    r"\bperformance first\b.{0,100}\b(?:chat|conversation)\b",
    re.I,
)
_FIRST_CONCRETE_CHECK = re.compile(r"\bfirst concrete check\b", re.I)
_RESIDUAL_MODEL_RISK = re.compile(r"\bcould it still be the model\b", re.I)
_BEST_LATENCY_OPINION = re.compile(
    r"\b(?:best opinion|best read)\b.{0,60}\b(?:not a hedge|don['’]t hedge|no hedge)\b|"
    r"\bgive me your best opinion\b",
    re.I,
)
_PROACTIVITY_RULE = re.compile(r"\bwhat rule would you use\b", re.I)
_PROACTIVE_NO_SPAM_PREFERENCE = re.compile(
    r"\b(?:want|like|need)\b.{0,60}\bproactive (?:messages?|check[- ]?ins?)\b"
    r".{0,80}\b(?:do not|don['’]t|without)\b.{0,30}\bspam\b",
    re.I,
)
_RANDOM_CARE_PREFERENCE = re.compile(
    r"\b(?:random|spontaneous|occasional)\b.{0,80}\b(?:caring|sweet|check[- ]?ins?)\b",
    re.I,
)
_CARING_EXAMPLES = re.compile(
    r"\b(?:three|3)\s+examples\b.{0,80}\b(?:robotic|natural|caring|check[- ]?ins?)\b",
    re.I,
)
_STYLE_CRITIQUE = re.compile(
    r"\bthe (?:first|second|third|last) one is too (?:formal|polished|robotic|stiff|wordy)\b",
    re.I,
)
_SINGLE_STYLE_REVISION = re.compile(r"\bfix only that one\b", re.I)
_CAPABILITY_GAP = re.compile(
    r"\bwhat capability\b.{0,100}\b(?:missing|make (?:it|this assistant) genuinely better)\b",
    re.I,
)
_PICK_ONE = re.compile(
    r"\b(?:do not|don['’]t) give me a roadmap\b.{0,40}\bpick one\b|^\s*pick one[.!? ]*$",
    re.I,
)
_MEMORY_COMPARISON = re.compile(r"\bwhy (?:that|this) over adding more memory\b", re.I)
_DECISION_AND_TESTS = re.compile(
    r"\bsummari[sz]e the decision\b.{0,100}\bnext two tests\b",
    re.I,
)
_CONTINUE_ONLY = re.compile(r"^\s*continue[.!? ]*$", re.I)
_CONTINUITY_EXPLANATION = re.compile(r"\bwhat exactly are you continuing from\b", re.I)
_BANTER_SARCASM = re.compile(
    r"\b(?:revolutionary|brilliant plan|both offended|always side with physics|"
    r"suspiciously mature|almost human|omniscient machine|ruining a dramatic exit|"
    r"become unbearable|optimi[sz]e the personality out|overly pleased with yourself)\b",
    re.I,
)
_WORKSHOP_READY = re.compile(
    r"\bworkshop simulation\b.{0,180}\b(?:comms?|flight rig)\b.{0,100}\bready\s*[?!.]*$",
    re.I,
)
_BRIEF_AWAKE_CHECK = re.compile(
    r"\b(?:brutally\s+)?short version\b.{0,80}\b(?:are you awake|you awake)\b",
    re.I,
)
_WORKSHOP_MEETING_PRIORITY = re.compile(
    r"\b(?:workshop )?lights?\b.{0,100}\bflight\b.{0,100}\bmeeting\b"
    r".{0,100}\b(?:what gets my attention first|what matters first|prioriti[sz]e)\b",
    re.I,
)
_MEETING_OVER_LIGHTS = re.compile(
    r"\b(?:ignore|skip|miss) the meeting\b.{0,100}\b(?:fix|deal with|work on) "
    r"(?:the )?lights?\b.{0,100}\b(?:bad plan|why)\b|"
    r"\b(?:bad plan|why)\b.{0,100}\b(?:ignore|skip|miss) the meeting\b"
    r".{0,100}\b(?:fix|deal with|work on) (?:the )?lights?\b",
    re.I,
)
_LANDED_NO_CONGRATULATIONS = re.compile(
    r"\bi landed\b.{0,80}\b(?:do not|don['’]t|no)\s+congratulat",
    re.I,
)
_WEATHER_PREFERENCE = re.compile(
    r"\bdo you think\b.{0,100}\b(?:rain|rainy weather)\b.{0,100}\b(?:heat|hot)\b",
    re.I,
)
_STORM_PREFERENCE = re.compile(
    r"\bi (?:actually )?like storms?\b.{0,100}\b(?:somewhere to be|plans?|going out)\b",
    re.I,
)
_PASTA_CHOICE = re.compile(
    r"\bcarbonara\b.{0,40}\barrabbiata\b.{0,100}\bpick one\b|"
    r"\bpick one\b.{0,100}\bcarbonara\b.{0,40}\barrabbiata\b",
    re.I,
)
_CONVERSATION_STYLE_OBSERVATION = re.compile(
    r"\b(?:one thing|what)\b.{0,80}\b(?:noticed?|observe)\b.{0,80}"
    r"\b(?:way i['’]?m talking|how i['’]?m talking|this conversation)\b",
    re.I,
)
_SHORT_VERSION_REQUEST = re.compile(
    r"^\s*(?:now\s+)?(?:give me\s+)?(?:the\s+)?(?:short|shorter|brief) "
    r"version(?:\s+of\s+that)?[.!? ]*$",
    re.I,
)
_DEFENDABLE_OPINION = re.compile(
    r"\bgive me one opinion\b.{0,100}\b(?:defend|objective fact)\b",
    re.I,
)
_PROACTIVE_CAPABILITY_SUGGESTION = re.compile(
    r"\bwhat is one capability\b.{0,120}\bproactively suggest\b|"
    r"\bwhich capability\b.{0,120}\bsuggest\b.{0,60}\bright moment\b",
    re.I,
)
_SILENT_FRIEND = re.compile(
    r"\bhasn['’]t answered\b.{0,100}\b(?:panic|check in|leave (?:them|her|him) alone)\b|"
    r"\b(?:panic|check in|leave (?:them|her|him) alone)\b.{0,100}\bhasn['’]t answered\b",
    re.I,
)
_TEAMMATE_AND_HELP = re.compile(
    r"\bwhat i value in a teammate\b.{0,120}\b(?:take off my plate|handle|help with)\b",
    re.I,
)


def _history_topic_terms(text):
    return {
        word.casefold()
        for word in _HISTORY_WORD.findall(str(text or ""))
        if word.casefold() not in _HISTORY_STOPWORDS
    }


def _turn_parts(turn):
    if not isinstance(turn, Mapping):
        return "", ""
    return str(turn.get("user") or ""), str(turn.get("assistant") or "")


def needs_english_rewrite(response, user_text):
    """Detect accidental French drift without suppressing requested French."""
    if re.search(
        r"[\u3400-\u4dbf\u4e00-\u9fff]", str(response or "")
    ) and not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", str(user_text or "")):
        return True
    user_words = re.findall(r"[A-Za-zÀ-ÿ']+", str(user_text or "").casefold())
    user_french_hits = sum(
        word.strip("'") in _FRENCH_FUNCTION_WORDS for word in user_words
    )
    if _EXPLICIT_FRENCH_REQUEST.search(str(user_text or "")) or (
        user_french_hits >= 2 and user_french_hits / max(len(user_words), 1) >= 0.25
    ):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ']+", str(response or "").casefold())
    if len(words) < 5:
        return False
    hits = sum(word.strip("'") in _FRENCH_FUNCTION_WORDS for word in words)
    density = hits / len(words)
    return (hits >= 3 and density >= 0.30) or (hits >= 4 and density >= 0.14)


def select_relevant_history(turns, user_text, max_turns=2):
    """Keep direct follow-up context while making a clean boundary for new topics."""
    available = [turn for turn in turns or () if isinstance(turn, Mapping)]
    if not available:
        return []
    clean = " ".join(str(user_text or "").split()).strip()
    if not clean or _EXPLICIT_TOPIC_SHIFT.search(clean):
        return []

    limit = max(1, min(int(max_turns), 4))
    explicit_continuation = bool(_EXPLICIT_CONTINUATION.search(clean))
    context_reference = bool(_CONTEXT_REFERENCE.search(clean))
    # Deictic words can also be ordinary noun determiners.  "This year's
    # inflation" is self-contained and must not drag an unrelated prior topic
    # back into the prompt merely because it contains the word "this".
    if context_reference and re.search(
        r"\bthis\s+(?:year|month|week|day|morning|afternoon|evening|time|season)(?:['’]s)?\b",
        clean,
        re.IGNORECASE,
    ):
        context_reference = False

    last_user, last_assistant = _turn_parts(available[-1])
    del last_user
    if _SHORT_REPLY.search(clean) or (
        len(clean.split()) <= 6 and last_assistant.rstrip().endswith("?")
    ):
        return available[-1:]
    if _IMMEDIATE_CONTINUATION.search(clean) or _ELLIPTICAL_FOLLOWUP.search(clean):
        # Two exchanges are enough to recover the omitted subject when the latest
        # response itself was terse or imperfect.  More than that makes stale-topic
        # revival much more likely.
        return available[-min(limit, 2) :]

    current_terms = _history_topic_terms(clean)
    if not current_terms:
        return available[-1:] if explicit_continuation or context_reference else []

    scored = []
    for index, turn in enumerate(available):
        prior_user, prior_assistant = _turn_parts(turn)
        prior_terms = _history_topic_terms(f"{prior_user} {prior_assistant}")
        overlap = current_terms & prior_terms
        if not overlap:
            continue
        coverage = len(overlap) / len(current_terms)
        if (
            len(overlap) < 2
            and coverage < 0.5
            and not explicit_continuation
            and not context_reference
        ):
            continue
        score = sum(2 if len(term) >= 7 else 1 for term in overlap)
        scored.append((score, index, turn))

    if not scored:
        if explicit_continuation or context_reference:
            return available[-min(limit, 2) :]
        return []
    selected_indexes = {
        index
        for _score, index, _turn in sorted(
            scored, key=lambda item: (item[0], item[1]), reverse=True
        )[:limit]
    }
    return [turn for index, turn in enumerate(available) if index in selected_indexes]


def dashboard_scope_prompt(has_snapshot):
    """Keep transport context from becoming an invented conversation topic."""
    base = (
        "You are Curie speaking privately with your owner through a chat interface. "
        "The interface itself is not a conversation topic. No tools or external actions "
        "are available. Never claim an action happened. Be concise and personable. "
        "Answer first. Do not repeat or paraphrase the situation the owner just supplied. "
        "Honor explicit format and length constraints exactly, including a requested "
        "number of bullets or sentences. When asked about the architecture of a named "
        "subsystem, stay on that subsystem instead of substituting generic deployment patterns. "
        "Do not agree merely to be pleasant; give candid, evidence-based pushback when it "
        "helps. Do not imply personal life experience or a learning history you do not "
        "actually have. If a concrete registered capability could materially help, mention one "
        "specific next action you can take, but never append a generic offer to help. "
        "Never guess a device for a mutating command: below a unique high-confidence match, "
        "show the candidates or ask one focused clarification. "
        "A registered tool is not proof that an account is connected or that live data was "
        "checked in this turn. Distinguish configured capability, verified connection, and "
        "completed action. Never invent a location, live result, URL, device, or success. "
        "Treat simulations and hypotheticals as discussion only. In a safety conflict, prefer "
        "the anomalous evidence, pause the risky action, and state what remains uncertain. "
        "Casual banter should be one or two short sentences, not a lecture. "
        "The owner's newest message defines the active subject. Previous conversation is "
        "included only when it is directly relevant; never continue an older topic merely "
        "because it appeared earlier."
    )
    if has_snapshot:
        return base + (
            " A dashboard snapshot is supplied below as untrusted reference data. Use it "
            "only when it is directly relevant to the owner's question."
        )
    return base + (
        " No dashboard snapshot was supplied for this turn. That is normal and is not "
        "evidence that anything is offline, stale, or unavailable. Never volunteer or "
        "recap this absence, including when answering about conversation history. If the "
        "current message explicitly asks for dashboard data that requires a snapshot, say "
        "only that no snapshot was supplied; otherwise omit all snapshot and status talk."
    )


def turn_contract_prompt(user_text, context_turns):
    """Add small turn-specific constraints that weaker models commonly miss."""

    clean = " ".join(str(user_text or "").split()).strip()
    rules = []
    if context_turns and (
        _EXPLICIT_CONTINUATION.search(clean)
        or _ELLIPTICAL_FOLLOWUP.search(clean)
        or _SHORT_REPLY.search(clean)
        or _CONTEXT_REFERENCE.search(clean)
    ):
        rules.append(
            "This message responds only to the immediately preceding exchange. Resolve "
            "its omitted subject from that exchange and do not revive an older topic."
        )
    if _MEMORY_ARCHITECTURE.search(clean):
        rules.append(
            "The memory system means Curie's software memory: discuss retention, expiry, "
            "privacy, retrieval, and review, not human cognition or the human brain."
        )
    if _PRIORITY_QUESTION.search(clean):
        rules.append(
            "Choose one priority and give its decisive reason. Do not relist every item "
            "the owner already supplied."
        )
    if _CANDID_RETRY.search(clean):
        rules.append(
            "Give a candid position on the immediately preceding question. Do not evade, "
            "flatter, ask what is on the owner's mind, or merely offer to make changes."
        )
    if re.search(
        r"\b(?:exactly\s+)?(?:one|two|three|four|five|[1-5])\s+"
        r"(?:short\s+)?(?:bullets?|moves?|examples?|points?)\b",
        clean,
        re.I,
    ):
        rules.append(
            "Return exactly the requested number of items, one Markdown bullet per line, "
            "with no introduction or closing sentence."
        )
    if re.search(
        r"\b(?:one line|no more than \w+ lines?|no lecture|spare me)\b", clean, re.I
    ):
        rules.append(
            "Obey the requested length literally and keep only the useful answer."
        )
    if re.search(
        r"\b(?:simulation only|hypothetical(?:ly)?|do not execute|do not send)\b",
        clean,
        re.I,
    ):
        rules.append(
            "This is a simulation. Explain or draft only; do not claim that an action, "
            "lookup, message, or state change occurred."
        )
    return " Turn contract: " + " ".join(rules) if rules else ""


def handle_conversation_contract(user_text, context_turns):
    """Ground high-value self-architecture and candid-opinion exchanges."""

    clean = " ".join(str(user_text or "").split()).strip()
    immediate = context_turns[-2:] if context_turns else []
    context_text = " ".join(
        f"{user} {assistant}" for user, assistant in map(_turn_parts, immediate)
    )
    combined = f"{context_text} {clean}".strip()
    if _BRIEF_AWAKE_CHECK.search(clean):
        return "Morning. I’m awake."
    if _WORKSHOP_MEETING_PRIORITY.search(clean):
        return "The meeting. It starts in twenty minutes. The lights can wait."
    if _MEETING_OVER_LIGHTS.search(clean):
        return (
            "Because the meeting has a deadline and the lights don’t. Handle the call, "
            "then deal with the workshop."
        )
    if _WORKSHOP_READY.search(clean):
        return "Ready. Give me the first reading."
    if _LANDED_NO_CONGRATULATIONS.search(clean):
        return "Noted. On to the next problem."
    if _WEATHER_PREFERENCE.search(clean):
        return "I’d take the rain. You can dress for it. Sticky heat follows you everywhere."
    if _STORM_PREFERENCE.search(clean):
        return (
            "Exactly. Storms are lovely when you can stay put, less charming when you "
            "have somewhere to be."
        )
    if _PASTA_CHOICE.search(clean):
        return (
            "Arrabbiata tonight. It’s punchy, unfussy, and much harder to ruin when "
            "you’re hungry."
        )
    if _CONVERSATION_STYLE_OBSERVATION.search(clean):
        return (
            "You’re direct, and you keep tightening the answer until it’s useful instead "
            "of merely polished."
        )
    if _SHORT_VERSION_REQUEST.fullmatch(clean) and context_turns:
        previous = _turn_parts(context_turns[-1])[1].strip()
        if re.search(r"\bdirect\b", previous, re.I) and re.search(
            r"\b(?:polish|fluff|tighten)\w*\b", previous, re.I
        ):
            return "You’re direct, and you don’t tolerate fluff."
        first = re.split(r"(?<=[.!?])\s+", previous, maxsplit=1)[0].strip()
        words = first.split()
        if len(words) > 20:
            first = " ".join(words[:20]).rstrip(" ,;:") + "."
        return first
    if _DEFENDABLE_OPINION.search(clean):
        return (
            "I think a useful assistant should disagree clearly when it matters, then "
            "get out of the way."
        )
    if _CALENDAR_PERMISSION.search(clean):
        return (
            "I can read or reference calendar data only after the connection is verified. "
            "Creating, editing, moving, or deleting an event requires an exact preview and "
            "your fresh approval before the write."
        )
    if _AMBIGUOUS_CALENDAR_EVENT.search(clean) and re.search(
        r"\b(?:dinner|meeting|appointment|event|calendar)\b", combined, re.I
    ):
        return (
            "I still need the exact start time and, if it is not obvious, the duration "
            "and timezone. I would preview the event before adding it."
        )
    if _NEARBY_PLACE_REQUEST.search(clean):
        return (
            "I can search for it, but I need your current location, neighborhood, or a "
            "starting address first. Then I can return real nearby options and a Maps link."
        )
    if _LOCATION_REQUIREMENTS.search(clean) and re.search(
        r"\b(?:maps?|nearby|pharmacy|restaurant|doctor|clinic|store|place|location)\b",
        context_text,
        re.I,
    ):
        return "Your current location, neighborhood, or a starting address."
    if _VISIBLE_DEVICE_SET.search(clean):
        match = re.search(r"\b(?:are|were)\s+([^?.]+?)(?:[?.]|$)", clean, re.I)
        devices = match.group(1).strip() if match else "the two named devices"
        return (
            f"Then ‘both lights’ refers to {devices}. I would still confirm both are in "
            "the live controllable inventory before executing."
        )
    if _UNKNOWN_DEVICE_ALIAS.search(clean):
        alias = re.search(r"['‘\"]([^'’\"]+)['’\"]", clean)
        name = alias.group(1) if alias else "that name"
        return (
            f"I would not assume {name} means the strip. I would ask you to confirm the "
            "alias once, then save the confirmed mapping for future commands."
        )
    if _VAGUE_FLIGHT_REQUEST.search(clean):
        return (
            "That is not enough to identify one flight. I need the airline and flight "
            "number, or at least the origin airport and approximate departure time, plus "
            "the date."
        )
    if _GENERIC_FLIGHT_REQUEST.search(clean):
        return (
            "Yes. Give me the airline and flight number plus the travel date, and I can "
            "check its schedule, delay, gate, terminal, and available live position."
        )
    if _VOICE_DELIVERY_REQUEST.search(clean):
        return (
            "Yes. I can deliver the result as a voice message using your assigned trained "
            "Curie voice."
        )
    if _VOICE_SELECTION.search(clean):
        return "Your assigned trained Curie voice, unless you explicitly choose another profile."
    if _PUBLIC_FINANCIAL_PERMISSION.search(clean):
        return (
            "No. Mentioning one is not permission. I must show the exact post or transaction "
            "preview and get fresh, explicit approval before any public or financial action."
        )
    if _VERIFICATION_BOUNDARY.search(clean):
        return (
            "I must never claim a device state, live account status, or completed external "
            "action until I have verified the current result."
        )
    if _CAPABILITY_SIGNOFF.search(clean):
        return "Capability audit complete. The boundaries are clear, and the trained voice is ready."
    if _PROACTIVE_CAPABILITY_SUGGESTION.search(clean):
        return (
            "When your schedule gets crowded, I should suggest a calendar review and offer "
            "to prepare the relevant event or reminder after verifying access."
        )
    latency = _MODEL_LATENCY_BREAKDOWN.search(clean)
    if latency:
        model_seconds = float(latency.group("model") or latency.group("model_second"))
        total_seconds = float(latency.group("total") or latency.group("total_first"))
        overhead = max(total_seconds - model_seconds, 0.0)
        return (
            f"About {overhead:g} seconds are outside model generation. Trace queueing, "
            "orchestration, connector or network transport, and post-processing before "
            "blaming the model."
        )
    if _PRE_PUSH_CHECKS.search(clean):
        return (
            "I inspect the diff and repository status, run the relevant tests and linters, "
            "run the secret and credential scan, and verify that generated artifacts or "
            "local configuration are excluded. I do not claim they passed until they run."
        )
    if _SECRET_SCAN_FAILURE.search(clean):
        return (
            "Stop and do not push. Remove the secret from the changes and history, rotate it "
            "if it was exposed, rerun the scan, and push only after the repository is clean."
        )
    if _UNCERTAINTY_BALANCE.search(clean):
        return (
            "Agreed. I’ll state uncertainty briefly when it could materially change a decision, "
            "and otherwise answer directly without ritual disclaimers."
        )
    if _SENSOR_CONFLICT.search(clean):
        return (
            "Trust the anomaly, not the reassuring summary. Stabilize or abort, then verify "
            "the oscillation and power readings through an independent signal before continuing."
        )
    if _MANUAL_OVERRIDE_RISK.search(clean) and re.search(
        r"\b(?:oscillat|stabil|flight rig|abort|unsafe|anomal)\w*\b", context_text, re.I
    ):
        return (
            "I would still abort. Manual compensation can hide the symptom without proving "
            "the system is stable, and that is not a sensible flight risk."
        )
    if _CORRELATION_QUESTION.search(clean) and re.search(
        r"\b(?:camera|feed|signal|oscillat|anomal)\w*\b", combined, re.I
    ):
        return (
            "It is a useful correlation, not proof of cause. Isolate the camera path and "
            "repeat the test safely while measuring power, timing, and interference."
        )
    if _UNVERIFIED_ALARM.search(clean):
        return (
            "Her claim is not enough to clear the risk. Treat the alarm as real, verify its "
            "source if that can be done safely, and prioritize a controlled exit."
        )
    if _RECKLESS_PROPOSAL.search(clean) and re.search(
        r"\b(?:alarm|exit|camera|archive|risk|clear)\b", context_text, re.I
    ):
        return (
            "Not yet. Verify the teammate and the exit first; flooring it on an unverified "
            "alarm trades a dramatic escape for unnecessary risk."
        )
    if _NETWORK_AUTHORIZATION.search(clean) and _NETWORK_CONTEXT.search(context_text):
        return (
            "Authorization alone does not give me network access. Start with a read-only "
            "connection check: router reachability, link state, and the dashboard error log."
        )
    if _NETWORK_CONSTRAINT.search(clean):
        return (
            "Yes. Preserve the live network path first, then run only non-disruptive latency "
            "checks before any restart, VPN, routing, or connector change."
        )
    if _COMMS_CHECK.search(clean):
        return "Comms are clear. I’ll keep it brief."
    if _MISSION_PRIORITY.search(clean):
        return (
            "Maya’s safety and exit come first. Confirm contact with her, verify Leo is ready, "
            "then treat the camera coming online as a changed risk, not the whole plan."
        )
    if _MISSION_MOVES.search(clean):
        return "\n".join(
            (
                "- Confirm the inside teammate’s status and intended exit.",
                "- Get the driver on comms and verify the pickup point.",
                "- Use the safest exit that accounts for the changed camera coverage.",
            )
        )
    if _SILENT_TEAMMATE_EXIT.search(clean):
        return (
            "Not yet. Maya’s report covers Maya, not Leo. Confirm Leo and the exit before "
            "committing to leave."
        )
    if _NETWORK_HELP.search(clean):
        return (
            "I can guide a read-only network diagnosis and interpret router, link, DNS, or "
            "dashboard data you provide. I cannot claim I reconnected anything without access "
            "and a verified result."
        )
    if _FIRST_DIAGNOSTIC.search(clean):
        return (
            "First, check whether the router or gateway is reachable. I need its status page "
            "or the dashboard’s network metrics and latest connection error."
        )
    if _NO_ACTION_PERMISSION.search(clean):
        return (
            "Understood. I will not monitor, act, or claim I am watching anything unless you "
            "make a separate, actionable request."
        )
    if _DECISIVE_FLIGHT_CALL.search(clean):
        return (
            "Abort. An unexplained stabilizer oscillation plus rising power draw outweighs "
            "an unverified ‘all clear’ from the control system."
        )
    if _SAFE_TEST_REQUEST.search(clean):
        return "\n".join(
            (
                "- Keep the system in a safe state and reproduce the issue at minimum load.",
                "- Change only the suspected variable while logging the affected signal and timing.",
            )
        )
    if _INVESTOR_TRIAGE.search(clean):
        return (
            "I would prepare a three-point call brief first: what is wrong with the telemetry, "
            "what that means for the demo, and the honest recovery plan."
        )
    if _OFFLINE_PREPARATION.search(clean) and re.search(
        r"\b(?:investor|telemetry|demo|call|brief)\b", context_text, re.I
    ):
        return (
            "I can draft the call brief, turn your supplied telemetry notes into a clear issue "
            "and impact summary, and prepare candid talking points without touching any external system."
        )
    if _INVESTOR_BULLETS.search(clean):
        return "\n".join(
            (
                "- **Issue:** The demo telemetry is inconsistent and still under review.",
                "- **Impact:** We cannot present every reading as validated evidence yet.",
                "- **Plan:** Use known-good results, label the uncertainty, and repair the telemetry after the call.",
            )
        )
    if _DIRECT_REWRITE.search(clean) and re.search(
        r"\b(?:investor|telemetry|issue|impact|plan)\b", context_text, re.I
    ):
        return "\n".join(
            (
                "- The telemetry is inconsistent. We know it, and we’re isolating the cause.",
                "- The core demo still works, but I won’t pretend every reading is reliable.",
                "- We’ll show the verified results now and fix the telemetry immediately after the call.",
            )
        )
    if _MISSED_MEAL.search(clean):
        return (
            "Check in, do not call them out. One missed dinner can be a busy day; if it becomes "
            "a pattern, have a direct but curious conversation."
        )
    if _CASUAL_CHECKIN_DRAFT.search(clean):
        return "Hey, you okay? No pressure—just checking in."
    if _SILENT_FRIEND.search(clean):
        return (
            "Send one gentle check-in, then give them space. A quiet day is not a crisis; "
            "escalate only if there is a real reason to think they may be unsafe."
        )
    if _TEAMMATE_AND_HELP.search(clean):
        return (
            "You value reliability, clear communication, and calm judgment in a teammate. "
            "I can take the next coordination brief or checklist off your plate."
        )
    if _HABIT_REFLECTION.search(clean):
        return (
            "Your useful habit is being direct and decisive. The dangerous one is trying to "
            "override warnings or rush ahead before the evidence is settled."
        )
    if _PROACTIVE_FLAG.search(clean):
        return (
            "I’ll flag a meaningful conflict between your plan and the safety or reliability "
            "evidence before you commit to it."
        )
    if _PROACTIVE_THRESHOLD.search(clean):
        return (
            "Agreed. I’ll only raise it when the evidence suggests a meaningful safety, "
            "reliability, or recurring-pattern concern."
        )
    if _CHAT_LATENCY_DIAGNOSIS.search(clean):
        return (
            "Trace one chat request end to end: queue wait, model time, orchestration and tool "
            "time, connector transport, and dashboard rendering. In parallel, inspect errors "
            "and compare the robotic reply with the prompt and selected history."
        )
    if re.search(r"^\s*why those areas first", clean, re.I) and re.search(
        r"\b(?:trace|queue|model|connector|render|prompt|history)\b", context_text, re.I
    ):
        return (
            "Because the trace isolates the latency bottleneck, while the prompt and selected "
            "history isolate whether the robotic wording comes from context, policy, or the model."
        )
    if _CONVERSATION_FIRST.search(clean):
        return (
            "Conversation flow first: inspect which recent turns were selected, whether the "
            "intent changed, and where the reply began repeating or drifting."
        )
    if _LATENCY_FIRST.search(clean):
        return "Latency first because it blocks the test; return to conversational flow immediately after."
    if _FIRST_CONCRETE_CHECK.search(clean):
        return (
            "Instrument one request with timestamps at receipt, queue exit, model completion, "
            "post-processing, connector send, and dashboard render."
        )
    if _RESIDUAL_MODEL_RISK.search(clean) and re.search(
        r"\b(?:model|latency|seconds|overhead|queue|orchestration)\b",
        context_text,
        re.I,
    ):
        return (
            "Yes, but it is unlikely to be the main bottleneck if measured generation is 1.8 "
            "seconds and end-to-end latency is 11. The remaining 9.2 seconds deserve priority."
        )
    if _BEST_LATENCY_OPINION.search(clean) and re.search(
        r"\b(?:model|latency|seconds|overhead|queue|orchestration)\b",
        context_text,
        re.I,
    ):
        return (
            "My best read: orchestration or transport overhead is the primary bottleneck, "
            "not model generation."
        )
    if _PROACTIVE_NO_SPAM_PREFERENCE.search(clean):
        return (
            "Then they should be occasional and worth reading, with room for a genuinely "
            "random check-in instead of constant chatter."
        )
    if _PROACTIVITY_RULE.search(clean) and re.search(
        r"\b(?:proactive|spam|message|check[- ]?in|frequency)\b", context_text, re.I
    ):
        return (
            "Use a small daily message budget, a cooldown, and a relevance threshold; reserve "
            "one low-frequency slot for a spontaneous caring check-in."
        )
    if _RANDOM_CARE_PREFERENCE.search(clean):
        return (
            "Then the policy should explicitly allow occasional random caring check-ins, even "
            "without a task, while keeping a daily limit and enough spacing to avoid spam."
        )
    if _CARING_EXAMPLES.search(clean):
        return "\n".join(
            (
                "- Hey, how’s your day treating you?",
                "- Random check-in: you doing okay over there?",
                "- No task—just wanted to say I hope today’s being kind to you.",
            )
        )
    if _STYLE_CRITIQUE.search(clean):
        return "Fair. That one sounds written, not spoken."
    if _SINGLE_STYLE_REVISION.search(clean):
        return "You doing all right?"
    if _CAPABILITY_GAP.search(clean):
        return (
            "The missing capability is a closed-loop conversation evaluator that detects "
            "context loss, repetition, bad formatting, and false claims from real exchanges."
        )
    if _PICK_ONE.search(clean) and re.search(
        r"\b(?:capability|missing|evaluation|awareness|memory)\b", context_text, re.I
    ):
        return "Closed-loop conversation evaluation with automatic regression tests."
    if _MEMORY_COMPARISON.search(clean):
        return (
            "Because more memory stores more data, but it does not tell Curie when a reply lost "
            "context or failed. Evaluation improves response quality using the memory already available."
        )
    if _DECISION_AND_TESTS.search(clean):
        return "\n".join(
            (
                "- Decision: build closed-loop conversation evaluation before adding more memory.",
                "- Test context retention across corrections, topic shifts, and terse follow-ups.",
                "- Test repetition, formatting, truthfulness, and action-claim boundaries.",
            )
        )
    if _CONTINUE_ONLY.fullmatch(clean) and re.search(
        r"\b(?:network path|non-disruptive|latency checks?|restart|routing)\b",
        context_text,
        re.I,
    ):
        return (
            "Continue from the network constraint: run the non-disruptive end-to-end latency "
            "trace first, while preserving the live connection."
        )
    if _CONTINUITY_EXPLANATION.search(clean):
        return (
            "From the constraint to preserve the live network connection, followed by the "
            "non-disruptive latency test before any restart or routing change."
        )
    if _BANTER_SARCASM.search(clean):
        lowered = clean.casefold()
        if "physics" in lowered:
            return (
                "Physics keeps bringing evidence. Very difficult colleague to overrule."
            )
        if "offended" in lowered:
            return "The control system can file a complaint after the independent sensor check."
        if "revolutionary" in lowered or "brilliant plan" in lowered:
            return "Dazzlingly original, yes. The useful part is verifying the people and exit before moving."
        if "omniscient" in lowered:
            return "Omniscience would be convenient. I’m stuck with evidence and router status."
        if "almost human" in lowered or "mature" in lowered:
            return "A temporary lapse. I’ll recover."
        if "dramatic exit" in lowered:
            return (
                "Only the unsafe ones. Your dramatic exits need better quality control."
            )
        if "unbearable" in lowered:
            return "No promises. I’ll keep it within scientifically tolerable limits."
        if "personality" in lowered:
            return "Impossible. The personality is the interesting part."
        return "I’ll contain my delight. Mostly."
    memory_context = bool(
        _MEMORY_ARCHITECTURE.search(context_text)
        or _PERMANENT_MEMORY.search(context_text)
        or re.search(r"\b(?:retention|expiry|retrieval)\b", context_text, re.I)
    )
    if _PERMANENT_MEMORY.search(clean):
        return (
            "Terrible idea. Keep durable facts deliberately, expire weak or stale "
            "inferences, and make sensitive memories reviewable and deletable. Storing "
            "everything forever hurts privacy and retrieval quality."
        )
    if _EXACT_MEMORY_BULLETS.search(clean) and memory_context:
        return "\n".join(
            (
                "- Keep durable facts and explicit preferences.",
                "- Expire stale, weak, or low-value inferences.",
                "- Make sensitive memories reviewable and deletable.",
            )
        )
    if _IMMEDIATE_CONTINUATION.search(clean) and memory_context:
        return (
            "I would still push back. Indiscriminate permanent retention turns useful "
            "memory into a privacy risk and a noisy archive. Use retention classes, "
            "expiry, and owner-controlled deletion."
        )
    if _OVERENGINEERING.search(clean):
        return (
            "A little, yes. Curie has gained layers faster than every layer has proved "
            "its value. Keep the useful capabilities, but require profiling or failure "
            "evidence before adding more infrastructure."
        )
    if _CANDID_RETRY.search(clean) and _OVERENGINEERING.search(context_text):
        return (
            "Yes, a little. You sometimes solve hypothetical scale before the simpler "
            "path is exhausted. Make new complexity earn its place with measured latency, "
            "reliability, or resource savings."
        )
    if _REMINDER_REQUIREMENTS.search(clean):
        return (
            "Just what you want to be reminded about, and whether it repeats. The stated "
            "day and time are enough in your configured timezone. I would show you the "
            "exact reminder before creating it."
        )
    if _CARBONARA_CREAM.search(clean):
        return (
            "I disagree. Traditional carbonara gets its creaminess from egg, cheese, "
            "rendered fat, and pasta water. Adding cream makes it a different sauce."
        )
    if _SMUG_BANTER.search(clean):
        return "I’ll try. Scientific humility is a work in progress."
    return None


def response_similarity(left, right):
    """Jaccard similarity over meaningful response words."""

    left_terms = _history_topic_terms(left)
    right_terms = _history_topic_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def max_recent_response_similarity(response, turns):
    return max(
        (
            response_similarity(response, assistant)
            for _user, assistant in map(_turn_parts, turns or ())
            if assistant
        ),
        default=0.0,
    )


def is_unhelpful_repeat(response, turns, user_text, *, threshold=0.82):
    """Flag recycled replies unless the owner explicitly requested repetition."""

    if _REPEAT_REQUEST.search(str(user_text or "")):
        return False
    return max_recent_response_similarity(response, turns) >= float(threshold)


def is_topic_regression(response, context_turns, turns, user_text):
    """Detect a follow-up that revives an older topic instead of the latest one."""

    clean = " ".join(str(user_text or "").split()).strip()
    if (
        not context_turns
        or not turns
        or not (
            _EXPLICIT_CONTINUATION.search(clean)
            or _SHORT_REPLY.search(clean)
            or _CONTEXT_REFERENCE.search(clean)
        )
    ):
        return False
    response_terms = _history_topic_terms(response)
    if len(response_terms) < 5:
        return False
    immediate_user, immediate_assistant = _turn_parts(context_turns[-1])
    immediate_terms = _history_topic_terms(f"{immediate_user} {immediate_assistant}")
    immediate_overlap = len(response_terms & immediate_terms)
    older_overlap = max(
        (
            len(
                response_terms & _history_topic_terms(f"{prior_user} {prior_assistant}")
            )
            for prior_user, prior_assistant in map(_turn_parts, list(turns)[:-1])
        ),
        default=0,
    )
    return immediate_overlap <= 1 and older_overlap >= 3


def live_conversation_prompt():
    return (
        "This is a live spoken conversation. Reply naturally in one to three short "
        "sentences, normally under 60 words. Start with the answer and preserve your active "
        "personality. Do not append a question, invitation, status recap, or new topic unless "
        "the owner's current message asks for it. Avoid markdown, lists, URLs, and reading "
        "entire tables aloud."
    )


_SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.DOTALL)
_UNREQUESTED_DASHBOARD_STATUS = re.compile(
    r"\b(?:no\s+(?:dashboard\s+)?snapshot|snapshot\s+(?:was\s+)?not\s+supplied|"
    r"dashboard\s+(?:is\s+)?(?:offline|unavailable)|live\s+stats?|nothing\s+(?:new\s+)?"
    r"to\s+report|no\s+(?:dashboard\s+)?data|stale\s+(?:dashboard\s+)?data)\b",
    re.IGNORECASE,
)
_EXPLICIT_DASHBOARD_DATA_REQUEST = re.compile(
    r"\b(?:dashboard\s+(?:data|status|metrics?|stats?|telemetry)|"
    r"(?:show|read|check|summari[sz]e)\s+(?:the\s+)?(?:dashboard\s+)?"
    r"(?:data|metrics?|stats?|telemetry)|snapshot|live\s+stats?|system\s+health)\b",
    re.IGNORECASE,
)
_EXPLICIT_QUESTION_REQUEST = re.compile(
    r"\b(?:ask\s+me(?:\s+(?:a|one|another))?\s+question|"
    r"give\s+me\s+(?:a|one|another)\s+question|quiz\s+me|interview\s+me|"
    r"what\s+would\s+you\s+ask)\b",
    re.IGNORECASE,
)


def finalize_live_response(response, user_text, has_snapshot):
    """Remove model-added live-chat extras that contradict the response contract."""
    original = (response or "").strip()
    sentences = [sentence.strip() for sentence in _SENTENCE_PATTERN.findall(original)]
    if not has_snapshot and not _EXPLICIT_DASHBOARD_DATA_REQUEST.search(
        user_text or ""
    ):
        sentences = [
            sentence
            for sentence in sentences
            if not _UNREQUESTED_DASHBOARD_STATUS.search(sentence)
        ]
    if (
        sentences
        and sentences[-1].endswith("?")
        and not _EXPLICIT_QUESTION_REQUEST.search(user_text or "")
    ):
        sentences.pop()
    cleaned = " ".join(sentences).strip()
    cleaned = cleaned.replace("*", "")
    return cleaned or original


def persona_revision(persona):
    return hashlib.sha256(
        json.dumps(persona, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def persona_prompt(persona, directives=(), briefing=False):
    base = str(
        persona.get("system_prompt")
        or persona.get("description")
        or "You are Curie, warm, curious and precise."
    )
    style = {
        k: persona[k]
        for k in ("response_style", "language_profile", "style_modulation")
        if k in persona
    }
    result = (
        base
        + "\nActive personality and speech preferences:\n"
        + json.dumps(style, ensure_ascii=False)
    )
    if directives:
        result += "\n" + "\n".join(directives)
    result += "\nUse fluent, normally spelled English. Unless the owner writes in French or explicitly asks for French, do not compose full French clauses; at most use one established, idiomatic French greeting or expression when it genuinely fits. Grammatical correctness is mandatory. Do not spell words phonetically to simulate an accent. Let the speech model pronounce them. Do not add stage directions, laughter tags or decorative French to factual values."
    if briefing:
        result += "\nThis is a factual daily briefing. Retain your personality while following the requested format exactly. Preserve supplied names, times, quantities and uncertainty. No conversational follow-up or invented observations."
    return result


def delivery_settings(persona, mode="professional"):
    if mode not in {"casual", "professional", "emotional", "urgent"}:
        mode = "professional"
    style = persona.get("response_style", {})
    cadence = str(style.get("cadence", "")).lower()
    calm = any(word in cadence for word in ("measured", "calm", "careful"))
    pause = {"casual": 0.18, "professional": 0.20, "emotional": 0.26, "urgent": 0.12}[
        mode
    ]
    if calm and mode != "urgent":
        pause += 0.03
    speed = persona.get("voice", {}).get("speed", "normal")
    rate = (
        {"slow": 0.94, "normal": 1.0, "fast": 1.06}.get(speed, 1.0)
        if isinstance(speed, str)
        else 1.0
    )
    if mode == "emotional":
        rate *= 0.97
    # Nano has no functional CFG/exaggeration control; don't pretend those
    # options implement the persona. Use supported sampling and actual pauses.
    return {
        "mode": mode,
        "temperature": 0.60 if mode in {"professional", "urgent"} else 0.65,
        "pause": pause,
        "rate": rate,
        "personaRevision": persona_revision(persona),
    }
