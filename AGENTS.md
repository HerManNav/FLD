# Project instructions

Use the `caveman` skill for all conversation about this project. Read
`C:/Users/German/.agents/skills/caveman/SKILL.md` and follow its concise style.
Keep code, comments, documentation, and other saved artifacts in normal English.
An explicit request for `normal mode` or `stop caveman` overrides this preference.

This is a small Windows desktop application. Keep dependencies minimal and keep
network operations off the GUI thread. Freenet downloads must use the local node.
Run `python -m unittest discover -s tests -v` after changes to download or parsing logic.

Before every feature or fix, read `docs/README.md`, then consult the linked
sections of `docs/development.md` and `docs/change-design.md` relevant to the
task. Do not reread unrelated sections or repeat repository-wide discovery
when this feature map already identifies the owner. Consult additional sections
when a change crosses their boundaries. Keep the index and affected references
current when behavior or architecture changes. Keep the root README concise
and focused on end-user use.

For all chats about this project, use gpt-6.1-sol with high reasoning for design
and gpt-6-luna with high reasoning for implementation. Write a concrete design
and acceptance conditions before handing work to the implementation agent.
Check actual model availability at runtime. If an exact requested model is
unavailable, tell the user and obtain approval for a named fallback before that
phase. Never claim Luna or Sol performed work unless that model actually ran.
Later explicit user instructions override this default workflow.
