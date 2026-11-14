--[[
	Studiocord helper plugin - Studiocord by JonasTheCoderX.

	Studio doesn't write the open script's name to its log, so this plugin tells the
	studiocord daemon (http://127.0.0.1:47231) which script is focused. It only sends
	the script's name and path; nothing else leaves Studio, and it only talks to localhost.

	Installed by install.sh into Studio's local Plugins folder.
]]

local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")
local StudioService = game:GetService("StudioService")
local Players = game:GetService("Players")
local ScriptEditorService = game:GetService("ScriptEditorService")

-- Only the edit DataModel; playtest DataModels show "Playtesting" anyway.
if not RunService:IsEdit() then
	return
end

local URL = "http://127.0.0.1:47231/script"
local _WATERMARK = "Studiocord by JonasTheCoderX" -- :)
local RESEND_SECONDS = 20

local lastSent, lastSentAt = nil, 0

local function lineCount(s)
	-- Prefer the editor's live text (includes unsaved edits), fall back to .Source.
	local ok, src = pcall(ScriptEditorService.GetEditorSource, ScriptEditorService, s)
	if not ok or type(src) ~= "string" then
		ok, src = pcall(function()
			return s.Source
		end)
	end
	if not ok or type(src) ~= "string" then
		return nil
	end
	local _, newlines = string.gsub(src, "\n", "")
	return newlines + 1
end

-- In Team Create, every collaborator in the session (you included) is a Player here.
local function collaborators()
	local ok, list = pcall(Players.GetPlayers, Players)
	return ok and #list or 0
end

local function current()
	local s = StudioService.ActiveScript
	if s == nil then
		return { name = "", path = "", class = "", collaborators = collaborators() }
	end
	return {
		name = s.Name,
		path = s:GetFullName(),
		class = s.ClassName,
		lines = lineCount(s),
		collaborators = collaborators(),
	}
end

local function send(force)
	local info = current()
	local body = HttpService:JSONEncode(info)
	if not force and body == lastSent then
		return
	end
	lastSent, lastSentAt = body, os.clock()
	task.spawn(function()
		-- Daemon not running is normal; stay quiet and retry on the next change/resend.
		pcall(HttpService.RequestAsync, HttpService, {
			Url = URL,
			Method = "POST",
			Headers = { ["Content-Type"] = "application/json" },
			Body = body,
		})
	end)
end

StudioService:GetPropertyChangedSignal("ActiveScript"):Connect(function()
	send(false)
end)
Players.PlayerAdded:Connect(function()
	send(false)
end)
Players.PlayerRemoving:Connect(function()
	task.defer(send, false) -- after the player is actually gone
end)

-- The script can be renamed while open; resend periodically so the daemon catches
-- that, and so it learns the state after it (re)starts.
task.spawn(function()
	while true do
		task.wait(2)
		send(os.clock() - lastSentAt >= RESEND_SECONDS)
	end
end)

send(true)
