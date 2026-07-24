Pls help me to do a plan. A followup to my Hermes Openbrain project. 
See last session 'Openbrain Funktionalität und Möglichkeiten'.
I want to build a web GUI to the Openbrain vector database.
See our project in Github: https://github.com/StephanSchipal/HermesPlusOpenbrain
Technical environment:
	Existing: VPS with docker under Hostinger (Ubuntu). 
	Plan for Web Gui: Frontend: react. Backend python. 
		Small DB for storing prompts and perhaps options (per user). And see Phase 1: deleted DB entries.
	Browser access via reverse proxy Traefik. (with Authentification)

Phase 1	
See GUI sketch: "D:\projects\claude\HermesPlusOpenbrain\planGuiProposal.png"
Png should be selfexplanatory. If not, pls ask questions, till you know it all.
All DB access via OpenBrain_MCP.
Rectangle 'List of keywords': (filled via OpenBrain_MCP): if clicked, entry should be copied to cursor position of textbox 'Prompt'.
Result grid, in entry rect: 'radio': radiobutton to select entry. For change or delete button enabling.
'Subject line' is generated out of summary of entry in the Openbrain_DB. (Could be slow, but no problem in Phase 1)
'Delete' button: if an Openbrain_DB entry is deleted (via MCP), it should be written in the small project DB.
'Change' button: Popup to change summary and keywords.
'Show delete log' button: show all delete entries of Openbrain_DB. Same like result grid entries. but with deleteion Datetime.

Phase 2
to be discussed after Phase 1
A dynamic wordcloud out of keywords.
See "D:\projects\claude\HermesPlusOpenbrain\Wordcloud.jpg"
Keywords bigger if more of the same. Should be selectable. For search buttons. one with and one with or.


Phase 3 
to be discussed after Phase 2
showing the capabilities of vetor database. (Clustering, classification)

Pls ask me questions till you a ready to develop a step by step plan.
This request to Claude is done on my Windows laptop with Claude Desktop and Claude Code.
Deployment then on Hostinger Docker env.

Resources: 
Github: 
	our project in Github: https://github.com/StephanSchipal/HermesPlusOpenbrain
	Openbrain repo by Nate Jones: https://github.com/NateBJones-Projects/OB1?shem=rimspwouoe,
	Hermes-Agent repo: https://github.com/nousresearch/hermes-agent
Internet: 
	Link to Hermes Documenatation: https://hermes-agent.nousresearch.com/docs/
	Link to features of Hermes-Agent: https://hermes-agent.org/#features
	Link to Documentation of Hermes on Hostinger: https://www.hostinger.com/support/how-to-get-started-with-hermes-agent-at-hostinger/
	Link to Hostinger: https://www.hostinger.com/
Youtube: 
	Wes Roth about Hermes-Agent: https://www.youtube.com/watch?v=bFO0uAMPx1g
	Nate Jones: Karpathy's Wiki vs OpenBrain: https://www.youtube.com/watch?v=dxq7WtWxi44
Project directory is HermesPlusOpenbrain: 
	Image of my Hostinger Dashboard: hostinger.jpg
   	Proposal for GUI: planGuiProposal.png