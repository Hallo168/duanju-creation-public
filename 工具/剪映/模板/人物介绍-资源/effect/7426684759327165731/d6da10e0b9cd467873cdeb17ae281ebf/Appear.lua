local exports = exports or {}
local Appear = Appear or {}
Appear.__index = Appear
function Appear.new(construct, ...)
    local self = setmetatable({}, Appear)
    self.text = nil
    self.tween = nil
    self.ease = Amaz.Ease.quadOutIn
    if construct and Appear.constructor then Appear.constructor(self, ...) end
    return self
end

function Appear:constructor()

end

function Appear:onStart(comp)
    local sprite = comp.entity:getComponent("Sprite2DRenderer")
    self.text = comp.entity:getComponent("SDFText")

    if sprite ~= nil then
        local material = sprite.material
        textureH = sprite:getTextureSize().y
        self.tween = comp.entity.scene.tween:fromTo(material, {["_alpha"] = 0.0}, {["_alpha"] = 1.0}, 0.1, Amaz.Ease.quadOut, nil, 0.0, nil, false)
    end

    if self.text ~= nil then
        textureH = self.text.rect.height
        self.tween = comp.entity.scene.tween:fromTo(self.text, {["alpha"] = 0.0}, {["alpha"] = 1.0}, 0.1, Amaz.Ease.quadOut, nil, 0.0, nil, false)
    end
end

function Appear:seek(time)
    self.tween:set(time)
end

function Appear:setDuration(duration)
    self.tween.duration = duration
end

function Appear:clear()
	if self.tween then
        self.tween:set(self.tween.duration)
		self.tween:clear()
		self.tween = nil
	end
end
exports.Appear = Appear
return exports
