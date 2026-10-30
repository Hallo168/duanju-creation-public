precision mediump float;
varying highp vec2 uv0;
varying highp vec2 uv;
uniform float ratio;
uniform float height_cut;

uniform sampler2D _MainTex;

void main(void)
{
    gl_FragColor = texture2D(_MainTex, uv0);
    gl_FragColor *= step(0., uv0.y);
    gl_FragColor *= step((1.-height_cut)*0.5 * .8, uv.y);
}
