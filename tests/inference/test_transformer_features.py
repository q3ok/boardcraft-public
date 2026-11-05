"""Transformer logits and optional trunk features have distinct stable outputs."""
import numpy as np
import pytest

from boardcraft.contracts import GameConfig
from boardcraft.inference.model import NumpyPolicy
from boardcraft.inference.spec import TokenLayout, weight_shapes
from boardcraft.inference.transformer import transformer
from boardcraft.registry import get_game


@pytest.mark.parametrize('batch_size',[1,3])
def test_transformer_features_flag_is_not_overwritten_by_entity_arrays(batch_size):
    module=get_game('dice_circuit');encoder=module.encoder()
    network=dict(family='transformer',layers=1,dim=4,heads=2,ff=8,activation='relu')
    rng=np.random.default_rng(21)
    weights={key:rng.normal(0,.1,shape).astype(np.float32) for key,shape in weight_shapes(encoder,network).items()}
    obs=module.new(GameConfig('dice_circuit','base',1),19).observe(0)
    x=np.stack([encoder.encode(obs)]*batch_size)
    layout=TokenLayout(encoder.encoder_spec())
    trunk=transformer(x,weights,network,layout,features=True)
    logits=transformer(x,weights,network,layout)
    assert trunk.shape==(batch_size,4)
    assert logits.shape==(batch_size,encoder.action_spec()['size'])
    np.testing.assert_allclose(logits,trunk@weights['pi.weight'].T+weights['pi.bias'],atol=1e-6,rtol=1e-6)
    head={'v.weight':np.full((1,4),.25,np.float32),'v.bias':np.array([.5],np.float32)}
    plain=NumpyPolicy(encoder,network,weights)
    valued=NumpyPolicy(encoder,network,weights,value_weights=head)
    np.testing.assert_array_equal(plain.logits(x),valued.logits(x))
    assert valued.value(x[0])==pytest.approx(float((trunk@head['v.weight'].T+head['v.bias'])[0,0]))
