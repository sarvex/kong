local helpers = require "spec.helpers"

local fmt = string.format

local CUSTOM_VAULTS = "./spec/fixtures/custom_vaults"
local CUSTOM_PLUGINS = "./spec/fixtures/custom_plugins"

local LUA_PATH = CUSTOM_VAULTS .. "/?.lua;" ..
                 CUSTOM_VAULTS .. "/?/init.lua;" ..
                 CUSTOM_PLUGINS .. "/?.lua;" ..
                 CUSTOM_PLUGINS .. "/?/init.lua;;"


local DUMMY_HEADER = "Dummy-Plugin"


-- using the full path so that we don't have to modify package.path in
-- this context
local test_vault = require "spec.fixtures.custom_vaults.kong.vaults.test"


---@class vault_test_harness
---
---@field name string
---
---@field config table
---
---@field update_secret fun(secret: string, value: string, options: table)
---
---@field setup fun()
---
---@field teardown fun()
---
---@field fixtures fun():table?
---
---@field prefix string
---
---@field host string


---@type vault_test_harness[]
local VAULTS = {
  {
    name = "test",

    config = {
      default_value = "DEFAULT",
      default_value_ttl = 1,
    },

    update_secret = test_vault.client.put,

    fixtures = function()
      return {
        http_mock = {
          test_vault = test_vault.http_mock,
        }
      }
    end,
  },
}


local noop = function(...) end

for _, vault in ipairs(VAULTS) do
  vault.prefix = vault.name .. "-ttl-test"
  vault.host = vault.name .. ".vault-ttl.test"

  vault.setup = vault.setup or noop
  vault.teardown = vault.teardown or noop
  vault.fixtures = vault.fixtures or noop
end


for _, strategy in helpers.each_strategy() do
for _, vault in ipairs(VAULTS) do

describe("vault ttl and rotation (#" .. strategy .. ") #" .. vault.name, function()
  local client
  local secret = "my-secret"


  local function http_get(path)
    path = path or "/"

    local res = client:get(path, {
      headers = {
        host = assert(vault.host),
      },
    })

    assert.response(res).has.status(200)

    return res
  end


  local function check_plugin_secret(expect)
    assert
      .with_timeout(120)
      .with_step(1)
      .eventually(function()
        local res = http_get("/")
        local value = assert.response(res).has.header(DUMMY_HEADER)

        if value == expect then
          return true
        end

        return nil, { expected = expect, got = value }
      end)
      .is_truthy(fmt("expected plugin secret for backend %s to be updated to %q",
                     vault.name, expect))
  end


  lazy_setup(function()
    helpers.setenv("KONG_LUA_PATH_OVERRIDE", LUA_PATH)

    vault.setup()

    local bp = helpers.get_db_utils(strategy,
                                    { "vaults", "routes", "services", "plugins" },
                                    { "dummy" },
                                    { vault.name })

    assert(bp.vaults:insert({
      name = vault.name,
      prefix = vault.prefix,
      config = vault.config,
    }))

    local route = assert(bp.routes:insert({
      name = vault.host,
      hosts = { vault.host },
      paths = { "/" },
      service = assert(bp.services:insert()),
    }))

    assert(bp.plugins:insert({
      name = "dummy",
      config = {
        resp_header_value = fmt("{vault://%s/%s?ttl=%s}",
                                vault.prefix, secret, 10),
      },
      route = { id = route.id },
    }))

    assert(helpers.start_kong({
      database = strategy,
      nginx_conf = "spec/fixtures/custom_nginx.template",
      vaults = vault.name,
      plugins = "dummy",
      log_level = "info",
    }, nil, nil, vault.fixtures() ))

    client = helpers.proxy_client()
  end)


  lazy_teardown(function()
    if client then
      client:close()
    end

    helpers.stop_kong(nil, true)
    vault.teardown()

    helpers.unsetenv("KONG_LUA_PATH_OVERRIDE")
  end)


  it("updates plugin config references (backend: #" .. vault.name .. ")", function()
    vault.update_secret(secret, "old", { ttl = 5 })
    check_plugin_secret("old")

    vault.update_secret(secret, "new", { ttl = 5 })
    check_plugin_secret("new")
  end)
end)

end -- each vault backend
end -- each strategy
