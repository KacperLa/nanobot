# What Is Life OS?

Most productivity software asks you to manage the system: create the task, categorize it, set the reminder, pick the project, maintain the board, check the calendar, and remember what mattered. Life OS is an experiment in flipping that relationship around.

Instead of being another app you operate, Life OS is a personal operating layer that watches your context, keeps track of what deserves attention, and lets an agent turn messy input into useful, visible structure.

At a high level, it is a feed of living cards backed by Nanobot. The feed is not organized like a traditional task app with rigid sections for tasks, events, notes, and reminders. It is organized by priority. The most relevant thing should rise to the top whether it is a task, a calendar event, the weather, an inbox item, a draft, a calorie tracker, or a temporary card the agent generated because it was useful in the moment.

## The Core Idea

Life OS treats your day as a stream of context rather than a database you need to maintain.

You can talk or type to the agent. You can quickly drop something into an inbox. The system can surface your calendar, weather, tasks, air quality, food tracking, drafts, watch cards, reading cards, maps, or shopping cards. The important part is not the individual card type. The important part is that cards are small, glanceable, and actionable.

The goal is to reduce the amount of effort required to answer questions like:

- What should I pay attention to right now?
- What did I commit to?
- What is coming up today?
- What did I mention that should become a task?
- What context should the agent already know before I ask?
- Can the agent turn this vague intention into the right tool, card, or next step?

That makes Life OS less like a todo list and more like an attention router.

## What It Does

Life OS combines several interaction modes into one surface.

The first is the card feed. Cards represent the current state of your life: tasks, inbox items, calendar timelines, weather, food logs, helper cards, generated drafts, and other agent-created artifacts. Cards are meant to be compact and useful, not dashboards full of decoration.

The second is the agent conversation. The chat interface is the main way to ask Nanobot to reason, create, edit, or operate on your context. Text interactions use a faster HTTP/SSE path, while voice mode can still use the richer real-time interface when needed.

The third is the inbox. This is where unprocessed thoughts land. A quick add button lets you capture something without deciding what it is yet. The same flow is designed to support future always-listening capture: listen, transcribe, summarize, distill, and forward important items into the inbox.

The fourth is dynamic cards. If the agent sees a task like "watch this video," it should be able to create a watch card. If it sees "email someone," it can create a draft card. If it sees "go somewhere," it can create a map or travel card. The user should not need to request the card explicitly every time. The agent should recognize when a visual or interactive artifact would help.

## How It Works

Nanobot is the agent runtime. It owns the conversation loop, tool calls, memory, sessions, channels, and integrations. Life OS uses Nanobot as the reasoning and action layer, then builds a personal interface on top of it.

The web UI is the attention layer. It shows the feed, chat, session drawer, voice mode, inbox, and cards. The UI is designed around fast switching between conversation and context rather than separating them into unrelated apps.

Cards are the interface contract. A card is a small HTML/CSS/JavaScript artifact with enough structure for the system to render, prioritize, edit, and pass as context to the agent. This matters because new use cases should not require rebuilding the core app. If the agent needs a new interactive card, it should be able to generate one with standard web primitives.

Tools are the action layer. Nanobot can read and write files, update tasks, manage cards, interact with calendar APIs, search, fetch, run controlled shell commands, and use configured MCP servers. The agent is not just producing text. It can modify the state that appears in the feed.

Priority is the organizing principle. Instead of keeping upcoming events below backlog tasks because they live in different sections, the feed should rank what matters. A persistent weather card can sit at the top because it is always useful in the morning. A high AQI alert should appear only when it actually matters. A snoozed all-day event should stop demanding attention.

## Why It Is Different

Many AI apps are chat boxes attached to tools. Many productivity apps are databases with reminders. Life OS is trying to be something in between: a personal context surface where the agent can create and maintain the interface itself.

That difference changes the design constraints.

The UI has to be fast because the user should not wait for a voice/WebRTC connection just to type a message. It has to be glanceable because attention is limited. It has to support dynamic cards because new real-world needs appear constantly. It has to preserve context without becoming noisy. It has to let the agent help without forcing the user to babysit every step.

The most interesting part is the workbench concept. Temporary visual artifacts can appear inside the chat as draftable, disposable objects. If they become important, they can be promoted into the main feed. This creates a path from conversation to durable attention without making every experiment permanent.

## Where It Is Going

The long-term direction is an ambient personal assistant that can capture, distill, and act.

Imagine saying something in passing, having the system recognize it as a possible task, putting it into the inbox, grooming it into a concrete action, attaching the right helper card, and surfacing it at the right time. Imagine your calendar, weather, location, tasks, and recent conversations all influencing what rises to the top without you manually organizing the system.

That is the promise of Life OS: not a smarter todo list, but a living interface for attention, context, and action.

It is still an experiment, but the shape is clear. The app should help you notice what matters, capture what would otherwise be lost, and let the agent build the small tools you need exactly when you need them.
