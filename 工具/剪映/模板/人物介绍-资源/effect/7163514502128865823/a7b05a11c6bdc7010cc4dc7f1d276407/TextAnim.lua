local function lerp(a, b, c)
	return (1 - c) * a + c * b
end

local function getBezierValue(controls, t)
    local ret = {}
    local xc1 = controls[1]
    local yc1 = controls[2]
    local xc2 = controls[3]
    local yc2 = controls[4]
    ret[1] = 3*xc1*(1-t)*(1-t)*t+3*xc2*(1-t)*t*t+t*t*t
    ret[2] = 3*yc1*(1-t)*(1-t)*t+3*yc2*(1-t)*t*t+t*t*t
    return ret
end

local function getBezierDerivative(controls, t)
    local ret = {}
    local xc1 = controls[1]
    local yc1 = controls[2]
    local xc2 = controls[3]
    local yc2 = controls[4]
    ret[1] = 3*xc1*(1-t)*(1-3*t)+3*xc2*(2-3*t)*t+3*t*t
    ret[2] = 3*yc1*(1-t)*(1-3*t)+3*yc2*(2-3*t)*t+3*t*t
    return ret
end

local function getBezierTfromX(controls, x)
    local ts = 0
    local te = 1
    -- divide and conque
    repeat
        local tm = (ts+te)/2
        local value = getBezierValue(controls, tm)
        if(value[1]>x) then
            te = tm
        else
            ts = tm
        end
    until(te-ts < 0.0001)

    return (te+ts)/2
end

local function bezier(controls)
	return function (t, b, c, d)
		t = t/d
		local tvalue = getBezierTfromX(controls, t)
		local value =  getBezierValue(controls, tvalue)
		return b + c * value[2]
	end
end

local function funcEaseBezier(t, b, c, d)
    t = t/d
    local controls = {.0,.0,0.5,1}
    local tvalue = getBezierTfromX(controls, t)
    local res = getBezierValue(controls, tvalue)
    return lerp(b,c,res[2])
end

local exports = exports or {}
local TextAnim = TextAnim or {}
TextAnim.__index = TextAnim
function TextAnim.new(construct, ...)
    local self = setmetatable({}, TextAnim)
    self.sharedMaterial = nil
    self.tween = nil
	self.materials = nil
    self.renderer = nil
    self.duration = 0
    if construct and TextAnim.constructor then TextAnim.constructor(self, ...) end
    return self
end

function TextAnim:constructor()

end

function TextAnim:onStart(comp) 
	self.text = comp.entity:getComponent('SDFText')
    if self.text == nil then
        local text = comp.entity:getComponent('Text')
        if text ~= nil then
			self.text = comp.entity:addComponent('SDFText')
            self.text:setTextWrapper(text)
        end
    end
    self.renderer = comp.entity:getComponent("MeshRenderer")
    self.parent = comp.entity:getComponent("Transform").parent
end

function TextAnim:initAnim()
    if self.first == nil then
        self.text.renderToRT = true
        local materials = Amaz.Vector()
        local InsMaterials = self.sharedMaterial:instantiate()
        materials:pushBack(InsMaterials)
        self.materials = materials
        self.renderer.materials = self.materials
        self.first = false
    end
end

function TextAnim:seek(time)
    self:initAnim()
    if time <= self.duration then
        self.text.renderToRT = true
        self.renderer.materials = self.materials
    else
        self.text.renderToRT = false
    end
    -- Amaz.LOGI("ldrldr3", self.materials:get(0):getTex("_MainTex").height)
    -- Amaz.LOGI("ldrldr4", self.text.rect.height)
    local mainTex = self.materials:get(0):getTex("_MainTex")
    if mainTex then

    end
    -- local texH = self.materials:get(0):getTex("_MainTex").height
    local texH1 = self.text:getRectExpanded().height
    local rectH = self.text.rect.height
    -- self.materials:get(0):setFloat("ratio", (1. - rectH / texH1) / 2.)
    local parent_s = self.parent.localScale.x
    -- Amaz.LOGI("ldr2 texH", texH)
    -- Amaz.LOGI("ldr4 texH1", texH1)
    -- Amaz.LOGI("ldr4 rectH", rectH)
    -- Amaz.LOGI("ldr4 ratio", rectH/(texH1*parent_s))
    -- Amaz.LOGI("ldr4 ps", parent_s)
    self.materials:get(0):setFloat("height_cut", rectH/(texH1))
    self.materials:get(0)["eraseUV"] = Amaz.Vector2f(0, -1) * (1. - funcEaseBezier(time / self.duration, 0, 1, 1))
end

function TextAnim:setDuration(duration)
    self.duration = duration
    if self.tween then
        self.tween.duration = duration
    end
end

function TextAnim:clear()
    self.text.renderToRT = false
end

function TextAnim:onEnter()
end

function TextAnim:onLeave()
    if self.text ~= nil then
		self:clear()
	end
end


exports.TextAnim = TextAnim
return exports
