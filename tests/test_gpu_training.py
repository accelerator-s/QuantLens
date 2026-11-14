import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


class DevicePolicy(unittest.TestCase):
    def test_auto_prefers_cuda_and_explicit_cpu_is_respected(self):
        from quantlens.gpu import use_gpu
        with patch('quantlens.gpu.worker_status',return_value={'cuda':True}), patch.dict('os.environ',{'QUANTLENS_TRAIN_DEVICE':'auto','QUANTLENS_COMPUTE_LOCAL':'0'}):
            self.assertTrue(use_gpu())
        with patch('quantlens.gpu.worker_status',return_value={'cuda':True}), patch.dict('os.environ',{'QUANTLENS_TRAIN_DEVICE':'cpu','QUANTLENS_COMPUTE_LOCAL':'0'}):
            self.assertFalse(use_gpu())
        with patch('quantlens.gpu.worker_status',return_value={}), patch.dict('os.environ',{'QUANTLENS_TRAIN_DEVICE':'auto','QUANTLENS_COMPUTE_LOCAL':'0'}):
            self.assertFalse(use_gpu())

    def test_cuda_preference_allows_missing_device(self):
        from quantlens.gpu import use_gpu
        from exceptions import ContractViolation
        with patch('quantlens.gpu.worker_status',return_value={}), patch.dict('os.environ',{'QUANTLENS_TRAIN_DEVICE':'cuda','QUANTLENS_COMPUTE_LOCAL':'0'}):
            self.assertFalse(use_gpu())


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Optional torch training dependency unavailable')
class TrainingMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        cls.torch=torch
        cls.devices=['cpu']+(['cuda'] if torch.cuda.is_available() else [])

    def test_linear_coefficients_match_least_squares_with_collinearity(self):
        from quantlens.gpu_worker import fit_linear
        rng=np.random.default_rng(42)
        x=rng.normal(size=(500,3));x=np.column_stack([x,x[:,0]])
        y=x@np.array([2.,-1.,.5,2.])
        expected=np.linalg.lstsq(x,y,rcond=None)[0]
        for device in self.devices:
            result=fit_linear(self.torch.tensor(x,device=device),self.torch.tensor(y,device=device)).cpu().numpy()
            np.testing.assert_allclose(result,expected,atol=1e-8)

    def test_logistic_learns_direction_and_is_finite(self):
        from quantlens.gpu_worker import fit_logistic
        x=np.random.default_rng(42).normal(size=(800,2));y=(2*x[:,0]-x[:,1]>0).astype(float)
        for device in self.devices:
            result=fit_logistic(self.torch.tensor(x,device=device),self.torch.tensor(y,device=device)).cpu().numpy()
            self.assertTrue(np.isfinite(result).all());self.assertGreater(result[0],0);self.assertLess(result[1],0)
            self.assertGreater(np.mean((x@result>0)==y),.97)

    def test_pca_preserves_covariance_subspace_and_variance(self):
        from quantlens.gpu_worker import fit_pca
        x=np.random.default_rng(42).normal(size=(500,3))*[5.,2.,.2]
        for device in self.devices:
            components,ratio=fit_pca(self.torch.tensor(x,device=device))
            matrix=components.cpu().numpy()
            np.testing.assert_allclose(matrix.T@matrix,np.eye(matrix.shape[1]),atol=1e-8)
            self.assertGreaterEqual(float(ratio.sum()),.8)
            self.assertLessEqual(matrix.shape[1],3)

    def test_kmeans_reports_actual_inertia(self):
        from quantlens.gpu_worker import fit_kmeans
        x=np.concatenate([np.random.default_rng(seed).normal(size=(30,2))*.1+offset for seed,offset in [(1,-4),(2,0),(3,4)]])
        for device in self.devices:
            centers,inertia=fit_kmeans(self.torch.tensor(x,device=device),3)
            actual=((x[:,None,:]-centers.cpu().numpy()[None,:,:])**2).sum(axis=2).min(axis=1).sum()
            self.assertAlmostEqual(inertia,actual,places=6);self.assertLess(inertia,3)


if __name__=='__main__':unittest.main()
