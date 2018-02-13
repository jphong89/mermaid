"""
Defines different registration methods as pyTorch networks.
Currently implemented:
    * SVFImageNet: image-based stationary velocity field
    * SVFMapNet: map-based stationary velocity field
    * SVFQuasiMomentumImageNet: EXPERIMENTAL (not working yet): SVF which is parameterized by a momentum
    * SVFScalarMomentumImageNet: image-based SVF using the scalar-momentum parameterization
    * SVFScalarMomentumMapNet: map-based SVF using the scalar-momentum parameterization
    * SVFVectorMomentumImageNet: image-based SVF using the vector-momentum parameterization
    * SVFVectorMomentumMapNet: map-based SVF using the vector-momentum parameterization
    * LDDMMShootingVectorMomentumImageNet: image-based LDDMM using the vector-momentum parameterization
    * LDDMMShootingVectorMomentumImageNet: map-based LDDMM using the vector-momentum parameterization
    * LDDMMShootingScalarMomentumImageNet: image-based LDDMM using the scalar-momentum parameterization
    * LDDMMShootingScalarMomentumImageNet: map-based LDDMM using the scalar-momentum parameterization
"""

import torch
import torch.nn as nn
from torch.autograd.variable import Variable
from torch.nn.parameter import Parameter

import rungekutta_integrators as RK
import forward_models as FM
from data_wrapper import AdaptVal
import regularizer_factory as RF
import similarity_measure_factory as SM

import smoother_factory as SF
import image_sampling as IS

from data_wrapper import MyTensor

import utils
import collections
import numpy as np

from abc import ABCMeta, abstractmethod

class RegistrationNet(nn.Module):
    """
    Abstract base-class for all the registration networks
    """
    __metaclass__ = ABCMeta

    def __init__(self, sz, spacing, params):
        """
        Constructor
        
        :param sz: image size (BxCxXxYxZ format) 
        :param spacing: spatial spacing, e.g., [0.1,0.1,0.2]
        :param params: ParameterDict() object to hold general parameters
        """
        super(RegistrationNet,self).__init__()
        self.sz = sz
        """image size"""
        self.spacing = spacing
        """image spacing"""
        self.params = params
        """ParameterDict() object for the parameters"""
        self.nrOfImages = sz[0]
        """the number of images, i.e., the batch size B"""
        self.nrOfChannels = sz[1]
        """the number of image channels, i.e., C"""

        self._shared_parameters = set()

    def get_variables_to_transfer_to_loss_function(self):
        """
        This is a function that can be overwritten by models to allow to return variables which are also 
        needed for the computation of the loss function. Returns None by default, but can for example be used
        to pass parameters or smoothers which are needed for the model itself and its loss. By convention
        these variables should be returned as a dictionary.
        :return: 
        """
        return None

    def get_custom_optimizer_output_string(self):
        """
        Can be overwritten by a method to allow for additional optimizer output (on top of the energy values)

        :return: 
        """
        return ''

    def get_custom_optimizer_output_values(self):
        """
        Can be overwritten by a method to allow for additional optimizer history output
        (should in most cases go hand-in-hand with the string returned by get_custom_optimizer_output_string()

        :return:
        """
        return None

    @abstractmethod
    def create_registration_parameters(self):
        """
        Abstract method to create the registration parameters over which should be optimized. They need to be of type torch Parameter() 
        """
        pass

    def get_registration_parameters(self):
        """
        Abstract method to return the registration parameters
        
        :return: returns the registration parameters 
        """
        return self.state_dict()

    def get_shared_registration_parameters(self):
        """
        Returns the parameters that have been declared shared for optimization.
        This can for example be parameters of a smoother that are shared between registrations.
        """
        cs = self.state_dict()
        shared_params = collections.OrderedDict()

        for key in cs:
            if self._shared_parameters.issuperset({key}):
                shared_params[key] = cs[key]
        return shared_params

    def set_registration_parameters(self, sd, sz, spacing):
        """
        Abstract method to set the registration parameters externally. This can for example be useful when the optimizer should be initialized at a specific value
        
        :param sd: model state dictionary
        :param sz: size of the image the parameter corresponds to 
        :param spacing: spacing of the image the parameter corresponds to 
        """
        self.load_state_dict(sd)
        self.sz = sz
        self.spacing = spacing


    def set_shared_registration_parameters(self, sd):
        """
        Allows to only set the shared registration parameters

        :param sd: dictionary containing the shared parameters
        :return: n/a
        """

        cs = self.state_dict()

        for key in sd:
            if cs.has_key(key):
               cs[key].copy_(sd[key])

    def downsample_registration_parameters(self, desiredSz):
        """
        Method to downsample the registration parameters spatially to a desired size. Should be overwritten by a derived class. 
        
        :param desiredSz: desired size in XxZxZ format, e.g., [50,100,40]
        :return: should return a tuple (downsampled_image,downsampled_spacing) 
        """
        raise NotImplementedError

    def upsample_registration_parameters(self, desiredSz):
        """
        Method to upsample the registration parameters spatially to a desired size. Should be overwritten by a derived class. 

        :param desiredSz: desired size in XxZxZ format, e.g., [50,100,40]
        :return: should return a tuple (upsampled_image,upsampled_spacing) 
        """
        raise NotImplementedError

#todo: maybe make in these cases the initial image explicitly part of the parameterization
#todo: this would then also allow for optimization over it
    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Convenience function to specify an image that should be visualized including its caption. 
        This will typically be related to the parameter of a model. This method should be overwritten by a derived class

        :param ISource: (optional) source image as this is part of the initial condition for some parameterizations
        :return: should return a tuple (image,desired_caption)
        """
        # not defined yet
        return None,None


class RegistrationNetDisplacement(RegistrationNet):
    """
        Abstract base-class for all the registration networks without time-integration
        which directly estimate a deformation field.
        """

    def __init__(self, sz, spacing, params):
        """
        Constructor

        :param sz: image size (BxCxXxYxZ format) 
        :param spacing: spatial spacing, e.g., [0.1,0.1,0.2]
        :param params: ParameterDict() object to hold general parameters
        """
        super(RegistrationNetDisplacement, self).__init__(sz,spacing,params)

        self.d = self.create_registration_parameters()
        """displacement field that will be optimized over"""

    def create_registration_parameters(self):
        """
        Creates the displacement field that is being optimized over
    
        :return: displacement field parameter 
        """
        return utils.create_ND_vector_field_parameter_multiN(self.sz[2::], self.nrOfImages)

    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Returns the displacement field parameter magnitude image and a name
    
        :return: Returns the tuple (displacement_magnitude_image,name) 
        """
        name = '|d|'
        par_image = ((self.d[:, ...] ** 2).sum(1)) ** 0.5  # assume BxCxXxYxZ format
        return par_image, name


    def upsample_registration_parameters(self, desiredSz):
        """
        Upsamples the displacement field to a desired size
    
        :param desiredSz: desired size of the upsampled displacement field 
        :return: returns a tuple (upsampled_state,upsampled_spacing)
        """
        sampler = IS.ResampleImage()
        ustate = self.state_dict().copy()
        upsampled_d, upsampled_spacing = sampler.upsample_image_to_size(self.d, self.spacing, desiredSz)
        ustate['d'] = upsampled_d.data

        return ustate, upsampled_spacing


    def downsample_registration_parameters(self, desiredSz):
        """
        Downsamples the displacemebt field to a desired size
    
        :param desiredSz: desired size of the downsampled displacement field 
        :return: returns a tuple (downsampled_state,downsampled_spacing)
        """
        sampler = IS.ResampleImage()
        dstate = self.state_dict().copy()
        dstate['d'], downsampled_spacing = sampler.downsample_image_to_size(self.d, self.spacing, desiredSz)
        return dstate, downsampled_spacing

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solved the map-based equation forward

        :param phi: initial condition for the map
        :param I0_source: not used
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the map with the displacement subtracted
        """
        return (phi-self.d)


class RegistrationNetTimeIntegration(RegistrationNet):
    """
        Abstract base-class for all the registration networks with time-integration
        """
    __metaclass__ = ABCMeta

    def __init__(self, sz, spacing, params):
        """
        Constructor

        :param sz: image size (BxCxXxYxZ format) 
        :param spacing: spatial spacing, e.g., [0.1,0.1,0.2]
        :param params: ParameterDict() object to hold general parameters
        """
        super(RegistrationNetTimeIntegration, self).__init__(sz,spacing,params)

        self.tFrom = 0.
        """time to solve a model from"""
        self.tTo = 1.
        """time to solve a model to"""

    @abstractmethod
    def create_integrator(self):
        """
        Abstract method to create an integrator for time-integration of a model
        """
        pass

class SVFNet(RegistrationNetTimeIntegration):
    """
    Base class for SVF-type registrations. Provides a velocity field (as a parameter) and an integrator
    """
    def __init__(self,sz,spacing,params):
        super(SVFNet, self).__init__(sz,spacing,params)
        self.v = self.create_registration_parameters()
        """velocity field that will be optimized over"""
        self.integrator = self.create_integrator()
        """integrator to do the time-integration"""

    def create_registration_parameters(self):
        """
        Creates the velocity field that is being optimized over
        
        :return: velocity field parameter 
        """
        return utils.create_ND_vector_field_parameter_multiN(self.sz[2::], self.nrOfImages)

    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Returns the velocity field parameter magnitude image and a name
        
        :return: Returns the tuple (velocity_magnitude_image,name) 
        """
        name = '|v|'
        par_image = ((self.v[:,...]**2).sum(1))**0.5 # assume BxCxXxYxZ format
        return par_image,name

    def upsample_registration_parameters(self, desiredSz):
        """
        Upsamples the velocity field to a desired size
        
        :param desiredSz: desired size of the upsampled velocity field 
        :return: returns a tuple (upsampled_state,upsampled_spacing)
        """
        sampler = IS.ResampleImage()
        ustate = self.state_dict().copy()
        upsampled_v,upsampled_spacing=sampler.upsample_image_to_size(self.v,self.spacing,desiredSz)
        ustate['v'] = upsampled_v.data

        return ustate,upsampled_spacing

    def downsample_registration_parameters(self, desiredSz):
        """
        Downsamples the velocity field to a desired size

        :param desiredSz: desired size of the downsampled velocity field 
        :return: returns a tuple (downsampled_image,downsampled_spacing)
        """
        sampler = IS.ResampleImage()
        dstate = self.state_dict().copy()
        dstate['v'],downsampled_spacing=sampler.downsample_image_to_size(self.v,self.spacing,desiredSz)
        return dstate,downsampled_spacing

class SVFImageNet(SVFNet):
    """
    Specialization for SVF-based image registration
    """
    def __init__(self, sz, spacing, params):
        super(SVFImageNet, self).__init__(sz,spacing,params)

    def create_integrator(self):
        """
        Creates an integrator for the advection equation of the image
        
        :return: returns this integrator 
        """
        cparams = self.params[('forward_model',{},'settings for the forward model')]
        advection = FM.AdvectImage(self.sz, self.spacing)
        return RK.RK4(advection.f, advection.u, self.v, cparams)

    def forward(self, I, variables_from_optimizer=None):
        """
        Solves the image-based advection equation
        
        :param I: initial condition for the image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the image at the final time (tTo)
        """
        I1 = self.integrator.solve([I], self.tFrom, self.tTo, variables_from_optimizer)
        return I1[0]


class SVFQuasiMomentumNet(RegistrationNetTimeIntegration):
    """
    Attempt at parameterizing SVF with a momentum-like vector field (EXPERIMENTAL, not working yet)
    """
    def __init__(self,sz,spacing,params):
        super(SVFQuasiMomentumNet, self).__init__(sz,spacing,params)
        self.m = self.create_registration_parameters()
        """momentum parameter"""
        cparams = params[('forward_model', {}, 'settings for the forward model')]
        self.smoother = SF.SmootherFactory(self.sz[2::], self.spacing).create_smoother(cparams)
        """smoother to go from momentum to velocity"""
        self._shared_parameters = self._shared_parameters.union(self.smoother.associate_parameters_with_module(self))
        """registers the smoother parameters so that they are optimized over if applicable"""
        self.v = torch.zeros_like(self.m)
        """corresponding velocity field"""

        self.integrator = self.create_integrator()
        """integrator to solve the forward model"""

    def get_custom_optimizer_output_string(self):
        return self.smoother.get_custom_optimizer_output_string()

    def get_custom_optimizer_output_values(self):
        return self.smoother.get_custom_optimizer_output_values()

    def create_registration_parameters(self):
        """
        Creates the registration parameters (the momentum field) and returns them
        
        :return: momentum field 
        """
        return utils.create_ND_vector_field_parameter_multiN(self.sz[2::], self.nrOfImages)

    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Returns the momentum magnitude image and :math:`|m|` as the image caption
        
        :return: Returns a tuple (magnitude_m,name) 
        """
        name = '|m|'
        par_image = ((self.m[:,...]**2).sum(1))**0.5 # assume BxCxXxYxZ format
        return par_image,name

    def upsample_registration_parameters(self, desiredSz):
        sampler = IS.ResampleImage()
        ustate = self.state_dict().copy()
        upsampled_m,upsampled_spacing=sampler.upsample_image_to_size(self.m,self.spacing,desiredSz)
        ustate['m'] = upsampled_m.data

        return ustate,upsampled_spacing

    def downsample_registration_parameters(self, desiredSz):
        sampler = IS.ResampleImage()
        dstate = self.state_dict().copy()
        dstate['m'],downsampled_spacing=sampler.downsample_image_to_size(self.m,self.spacing,desiredSz)
        return dstate,downsampled_spacing

class SVFQuasiMomentumImageNet(SVFQuasiMomentumNet):
    """
    Specialization for image registation
    """
    def __init__(self, sz, spacing, params):
        super(SVFQuasiMomentumImageNet, self).__init__(sz,spacing,params)

    def create_integrator(self):
        """
        Creates the integrator that solve the advection equation (based on the smoothed momentum)
        :return: returns this integrator
        """
        cparams = self.params[('forward_model',{},'settings for the forward model')]
        advection = FM.AdvectImage(self.sz, self.spacing)
        return RK.RK4(advection.f, advection.u, self.v, cparams)

    def forward(self, I, variables_from_optimizer=None):
        """
        Solves the model by first smoothing the momentum field and then using it as the velocity for the advection equation
        
        :param I: initial condition for the image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the image at the final time point (tTo)
        """
        self.smoother.smooth(self.m,self.v,[I,False],variables_from_optimizer)
        I1 = self.integrator.solve([I], self.tFrom, self.tTo, variables_from_optimizer)
        return I1[0]

class RegistrationLoss(nn.Module):
    """
    Abstract base class to define a loss function for image registration
    """
    __metaclass__ = ABCMeta

    def __init__(self,sz_sim,spacing_sim,sz_model,spacing_model,params):
        """
        Constructor. We have two different spacings to be able to allow for low-res transformation
        computations and high-res image similarity. This is only relevant for map-based approaches.
        For image-based approaches these two values should be the same.
        
        :param sz_sim: image/map size to evaluate the similarity measure of the loss
        :param spacing_sim: image/map spacing to evaluate the similarity measure of the loss
        :param sz_model: sz of the model parameters (will only be different from sz_sim if computed at low res)
        :param spacing_model: spacing of model parameters (will only be different from spacing_sim if computed at low res)
        :param params: ParameterDict() object to hold and keep track of general parameters
        """
        super(RegistrationLoss, self).__init__()
        self.spacing_sim = spacing_sim
        """image/map spacing for the similarity measure part of the loss function"""
        self.spacing_model = spacing_model
        """spacing for any model parameters (typically for the regularization part of the loss function)"""
        self.sz_sim = sz_sim
        """image size for the similarity measure part of the loss function"""
        self.sz_model = sz_model
        """image size for the model parameters (typically for the regularization part of the loss function)"""
        self.params = params
        """ParameterDict() paramters"""

        self.smFactory = SM.SimilarityMeasureFactory(self.spacing_sim)
        """factory to create similarity measures on the fly"""
        self.similarityMeasure = None
        """the similarity measure itself"""


    def add_similarity_measure(self, simName, simMeasure):
        """
        To add a custom similarity measure to the similarity measure factory
        
        :param simName: desired name of the similarity measure (string) 
        :param simMeasure: similarity measure itself (to instantiate an object)
        """
        self.smFactory.add_similarity_measure(simName,simMeasure)

    def compute_similarity_energy(self, I1_warped, I1_target, I0_source=None, phi=None, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computing the image matching energy based on the selected similarity measure
        
        :param I1_warped: warped image at time tTo 
        :param I1_target: target image to register to
        :param I0_source: source image at time 0 (typically not used)
        :param phi: map to warp I0_source to target space (typically not used)
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the value for image similarity energy
        """
        if self.similarityMeasure is None:
            self.similarityMeasure = self.smFactory.create_similarity_measure(self.params)
        sim = self.similarityMeasure.compute_similarity_multiNC(I1_warped, I1_target, I0_source, phi)
        return sim

    @abstractmethod
    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Abstract method computing the regularization energy based on the registration parameters and (if desired) the initial image
        
        :param I0_source: Initial image 
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: should return the value for the regularization energy
        """
        pass


class RegistrationImageLoss(RegistrationLoss):
    """
    Specialization for image-based registration losses
    """

    def __init__(self,sz_sim,spacing_sim,sz_model,spacing_model,params):
        super(RegistrationImageLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)

    def get_energy(self, I1_warped, I0_source, I1_target, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the overall registration energy as E = E_sim + E_reg
        
        :param I1_warped: warped image 
        :param I0_source: source image
        :param I1_target: target image
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: return the energy value
        """
        sim = self.compute_similarity_energy(I1_warped, I1_target, I0_source, None, variables_from_forward_model, variables_from_optimizer)
        reg = self.compute_regularization_energy(I0_source, variables_from_forward_model, variables_from_optimizer)
        energy = sim + reg
        return energy, sim, reg

    def forward(self, I1_warped, I0_source, I1_target, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the loss by evaluating the energy
        :param I1_warped: warped image
        :param I0_source: source image
        :param I1_target: target image
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: tuple: overall energy, similarity energy, regularization energy
        """
        energy, sim, reg = self.get_energy(I1_warped, I0_source, I1_target, variables_from_forward_model, variables_from_optimizer)
        return energy, sim, reg


class RegistrationMapLoss(RegistrationLoss):
    """
    Specialization for map-based registration losses
    """
    def __init__(self, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(RegistrationMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        cparams = params[('loss', {}, 'settings for the loss function')]
        self.display_max_displacement = cparams[('display_max_displacement',False,'displays the current maximal displacement')]
        self.limit_displacement = cparams[('limit_displacement',False,'[True/False] if set to true limits the maximal displacement based on the max_displacement_setting')]
        max_displacement = cparams[('max_displacement',0.05,'Max displacement penalty added to loss function of limit_displacement set to True')]
        self.max_displacement_sqr = max_displacement**2

    def get_energy(self, phi0, phi1, I0_source, I1_target, lowres_I0, variables_from_forward_model=None, variables_from_optimizer=None ):
        """
        Compute the energy by warping the source image via the map and then comparing it to the target image
        
        :param phi0: map (initial map from which phi1 is computed by integration; likely the identity map) 
        :param phi1: map (mapping the source image to the target image, defined in the space of the target image) 
        :param I0_source: source image
        :param I1_target: target image
        :param lowres_I0: for map with reduced resolution this is the downsampled source image, may be needed to compute the regularization energy
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: registration energy
        """
        I1_warped = utils.compute_warped_image_multiNC(I0_source, phi1, self.spacing_sim)
        sim = self.compute_similarity_energy(I1_warped, I1_target, I0_source, phi1, variables_from_forward_model, variables_from_optimizer)
        if lowres_I0 is not None:
            reg = self.compute_regularization_energy(lowres_I0, variables_from_forward_model, variables_from_optimizer)
        else:
            reg = self.compute_regularization_energy(I0_source, variables_from_forward_model, variables_from_optimizer)

        if self.limit_displacement:
            # first compute squared displacement
            dispSqr = ((phi1-phi0)**2).sum(1)
            if self.display_max_displacement==True:
                dispMax = ( torch.sqrt( dispSqr ) ).max()
                print( 'Max disp = ' + str( utils.t2np( dispMax )))
            sz = dispSqr.size()

            # todo: remove once pytorch can properly deal with infinite values
            maxDispSqr = utils.remove_infs_from_variable(dispSqr).max() # required to shield this from inf during the optimization

            dispPenalty = (torch.max((maxDispSqr - self.max_displacement_sqr),
                                     Variable(MyTensor(sz).zero_(), requires_grad=False))).sum()

            reg = reg + dispPenalty
        else:
            if self.display_max_displacement==True:
                dispMax = ( torch.sqrt( ((phi1-phi0)**2).sum(1) ) ).max()
                print( 'Max disp = ' + str( utils.t2np( dispMax )))

        energy = sim + reg
        return energy, sim, reg

    def forward(self, phi0, phi1, I0_source, I1_target, lowres_I0, variables_from_forward_model=None, variables_from_optimizer=None ):
        """
        Compute the loss function value by evaluating the registration energy
        
        :param phi0: map (initial map from which phi1 is computed by integration; likely the identity map) 
        :param phi1:  map (mapping the source image to the target image, defined in the space of the target image) 
        :param I0_source: source image
        :param I1_target: target image
        :param lowres_I0: for map with reduced resolution this is the downsampled source image, may be needed to compute the regularization energy
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: tuple: overall energy, similarity energy, regularization energy
        """
        energy, sim, reg = self.get_energy(phi0, phi1, I0_source, I1_target, lowres_I0, variables_from_forward_model, variables_from_optimizer)
        return energy,sim,reg


class SVFImageLoss(RegistrationImageLoss):
    """
    Loss specialization for image-based SVF 
    """
    def __init__(self,v,sz_sim,spacing_sim,sz_model,spacing_model,params):
        """
        Constructor
        
        :param v: velocity field parameter
        :param sz_sim: image/map size to evaluate the similarity measure of the loss
        :param spacing_sim: image/map spacing to evaluate the similarity measure of the loss
        :param sz_model: sz of the model parameters (will only be different from sz_sim if computed at low res)
        :param spacing_model: spacing of model parameters (will only be different from spacing_sim if computed at low res)
        :param params: general parameters via ParameterDict()
        """
        super(SVFImageLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.v = v
        """veclocity field parameter"""

        cparams = params[('loss',{},'settings for the loss function')]

        self.regularizer = (RF.RegularizerFactory(self.spacing_model).
                            create_regularizer(cparams))
        """regularizer to compute the regularization energy"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=False):
        """
        Computing the regularization energy
        
        :param I0_source: source image (not used)
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """

        batch_size = self.v.size()[0]
        return self.regularizer.compute_regularizer_multiN(self.v)/batch_size


class SVFQuasiMomentumImageLoss(RegistrationImageLoss):
    """
    Loss function specialization for the image-based quasi-momentum SVF implementation.
    Essentially the same as for SVF but has to smooth the momentum field first to obtain the velocity field.
    """
    def __init__(self,m,sz_sim,spacing_sim,sz_model,spacing_model,params):
        """
        Constructor
        
        :param m: momentum field
        :param sz_sim: image/map size to evaluate the similarity measure of the loss
        :param spacing_sim: image/map spacing to evaluate the similarity measure of the loss
        :param sz_model: sz of the model parameters (will only be different from sz_sim if computed at low res)
        :param spacing_model: spacing of model parameters (will only be different from spacing_sim if computed at low res)
        :param params: ParameterDict() parameters
        """
        super(SVFQuasiMomentumImageLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.m = m
        """vector momentum"""

        cparams = params[('loss',{},'settings for the loss function')]

        self.regularizer = (RF.RegularizerFactory(self.spacing_model).
                            create_regularizer(cparams))
        """regularizer to compute the regularization energy"""
        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
        else:
            cparams = self.params[('forward_model', {}, 'settings for the forward model')]

        #TODO: support smoother optimization here -> move smoother to model instead of loss function
        self.smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
        """smoother to convert from momentum to velocity"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Compute the regularization energy from the momentum
        
        :param I0_source: not used
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = self.m
        v = self.smoother.smooth(m,None,[I0_source,False],variables_from_optimizer)
        batch_size = self.m.size()[0]
        return self.regularizer.compute_regularizer_multiN(v)/batch_size + self.smoother.get_penalty()

class SVFMapNet(SVFNet):
    """
    Network specialization to a map-based SVF 
    """
    def __init__(self,sz,spacing,params):
        super(SVFMapNet, self).__init__(sz,spacing,params)

    def create_integrator(self):
        """
        Creates an integrator to solve a map-based advection equation
        
        :return: returns this integrator
        """
        cparams = self.params[('forward_model',{},'settings for the forward model')]
        advectionMap = FM.AdvectMap( self.sz, self.spacing )
        return RK.RK4(advectionMap.f,advectionMap.u,self.v,cparams)

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solved the map-based equation forward
        
        :param phi: initial condition for the map
        :param I0_source: not used
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the map at time tTo
        """
        phi1 = self.integrator.solve([phi], self.tFrom, self.tTo, variables_from_optimizer)
        return phi1[0]


class SVFMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss function for SVF to a map-based solution
    """
    def __init__(self,v,sz_sim,spacing_sim,sz_model,spacing_model,params):
        super(SVFMapLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.v = v
        """velocity field parameter"""


        cparams = params[('loss',{},'settings for the loss function')]

        self.regularizer = (RF.RegularizerFactory(self.spacing_model).
                            create_regularizer(cparams))
        """regularizer to compute the regularization energy"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the regularizaton energy from the velocity field parameter
        
        :param I0_source: not used 
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """

        batch_size = self.v.size()[0]
        return self.regularizer.compute_regularizer_multiN(self.v)/batch_size

class DiffusionMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss function for displacement-based registration to diffusion registration
    """

    def __init__(self, d, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(DiffusionMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.d = d
        """displacement field parameter"""

        cparams = params[('loss', {}, 'settings for the loss function')]

        self.regularizer = (RF.RegularizerFactory(self.spacing_model).
                            create_regularizer_by_name('diffusion',cparams))
        """regularizer to compute the regularization energy"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the regularizaton energy from the velocity field parameter

        :param I0_source: not used 
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """

        batch_size = self.d.size()[0]
        return self.regularizer.compute_regularizer_multiN(self.d)/batch_size

class TotalVariationMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss function for displacement-based registration to diffusion registration
    """

    def __init__(self, d, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(TotalVariationMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.d = d
        """displacement field parameter"""

        cparams = params[('loss', {}, 'settings for the loss function')]

        self.regularizer = (RF.RegularizerFactory(self.spacing_model).
                            create_regularizer_by_name('totalVariation',cparams))
        """regularizer to compute the regularization energy"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the regularizaton energy from the velocity field parameter

        :param I0_source: not used 
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """

        batch_size = self.d.size()[0]
        return self.regularizer.compute_regularizer_multiN(self.d)/batch_size

class CurvatureMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss function for displacement-based registration to diffusion registration
    """

    def __init__(self, d, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(CurvatureMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.d = d
        """displacement field parameter"""

        cparams = params[('loss', {}, 'settings for the loss function')]

        self.regularizer = (RF.RegularizerFactory(self.spacing_model).
                            create_regularizer_by_name('curvature',cparams))
        """regularizer to compute the regularization energy"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the regularizaton energy from the velocity field parameter

        :param I0_source: not used 
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """

        batch_size = self.d.size()[0]
        return self.regularizer.compute_regularizer_multiN(self.d)/batch_size

class AffineMapNet(RegistrationNet):
    """
    Registration network for affine transformation
    """
    def __init__(self,sz,spacing,params):
        super(AffineMapNet, self).__init__(sz,spacing,params)
        self.dim = len(self.sz) - 2
        self.Ab = self.create_registration_parameters()

    def create_registration_parameters(self):
        pars = Parameter(AdaptVal(torch.zeros(self.nrOfImages,self.dim*self.dim+self.dim)))
        utils.set_affine_transform_to_identity_multiN(pars.data)
        return pars

    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Returns the velocity field parameter magnitude image and a name

        :return: Returns the tuple (velocity_magnitude_image,name) 
        """
        name = 'Ab'
        par_image = self.Ab
        return par_image, name

    def upsample_registration_parameters(self, desiredSz):
        """
        Upsamples the afffine parameters to a desired size (ie., just returns them)

        :param desiredSz: desired size of the upsampled image
        :return: returns a tuple (upsampled_state,upsampled_spacing)
        """
        ustate = self.state_dict().copy() # stays the same
        upsampled_spacing = self.spacing*(self.sz[2::].astype('float')/desiredSz[2::].astype('float'))

        return ustate, upsampled_spacing

    def downsample_registration_parameters(self, desiredSz):
        """
        Downsamples the affine parameters to a desired size (ie., just returns them)

        :param desiredSz: desired size of the downsampled image 
        :return: returns a tuple (downsampled_state,downsampled_spacing)
        """
        dstate = self.state_dict().copy() # stays the same
        downsampled_spacing = self.spacing*(self.sz[2::].astype('float')/desiredSz[2::].astype('float'))
        return dstate, downsampled_spacing

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solved the map-based equation forward

        :param phi: initial condition for the map
        :param I0_source: not used
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the map at time tTo
        """
        phi1 = utils.apply_affine_transform_to_map_multiNC(self.Ab,phi)
        return phi1


class AffineMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss function for a map-based affine transformation
    """

    def __init__(self, Ab, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(AffineMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.Ab = Ab
        """affine parameters"""

    def compute_regularization_energy(self, I0_source, variables_from_forward_model=None, variables_from_optimizer=None):
        """
        Computes the regularizaton energy from the affine parameter

        :param I0_source: not used 
        :param variables_from_forward_model: (not used)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        regE = Variable(MyTensor(1).zero_(), requires_grad=False)
        return regE # so far there is no regularization


class ShootingVectorMomentumNet(RegistrationNetTimeIntegration):
    """
    Specialization to vector-momentum-based shooting for LDDMM
    """
    def __init__(self,sz,spacing,params):
        super(ShootingVectorMomentumNet, self).__init__(sz, spacing, params)
        self.m = self.create_registration_parameters()
        cparams = params[('forward_model', {}, 'settings for the forward model')]
        self.smoother = SF.SmootherFactory(self.sz[2::], self.spacing).create_smoother(cparams)
        """smoother"""
        self._shared_parameters = self._shared_parameters.union(self.smoother.associate_parameters_with_module(self))
        """registers the smoother parameters so that they are optimized over if applicable"""

        if params['forward_model']['smoother']['type'] == 'adaptiveNet':
            self.add_module('mod_smoother', self.smoother.smoother)
        """vector momentum"""
        self.integrator = self.create_integrator()
        """integrator to solve EPDiff variant"""

    def get_custom_optimizer_output_string(self):
        return self.smoother.get_custom_optimizer_output_string()

    def get_custom_optimizer_output_values(self):
        return self.smoother.get_custom_optimizer_output_values()

    def get_variables_to_transfer_to_loss_function(self):
        d = dict()
        d['smoother'] = self.smoother
        return d

    def create_registration_parameters(self):
        """
        Creates the vector momentum parameter
        
        :return: Returns the vector momentum parameter 
        """
        return utils.create_ND_vector_field_parameter_multiN(self.sz[2::], self.nrOfImages)

    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Creates a magnitude image for the momentum and returns it with name :math:`|m|`
        
        :return: Returns tuple (m_magnitude_image,name) 
        """
        name = '|m|'
        par_image = ((self.m[:,...]**2).sum(1))**0.5 # assume BxCxXxYxZ format
        return par_image,name

    def upsample_registration_parameters(self, desiredSz):
        """
        Upsamples the vector-momentum parameter
        
        :param desiredSz: desired size of the upsampled momentum 
        :return: Returns tuple (upsampled_state,upsampled_spacing)
        """

        ustate = self.state_dict().copy()
        sampler = IS.ResampleImage()
        upsampled_m, upsampled_spacing = sampler.upsample_image_to_size(self.m, self.spacing, desiredSz)
        ustate['m'] = upsampled_m.data

        return ustate,upsampled_spacing

    def downsample_registration_parameters(self, desiredSz):
        """
        Downsamples the vector-momentum parameter

        :param desiredSz: desired size of the downsampled momentum 
        :return: Returns tuple (downsampled_state,downsampled_spacing)
        """

        dstate = self.state_dict().copy()
        sampler = IS.ResampleImage()
        dstate['m'],downsampled_spacing=sampler.downsample_image_to_size(self.m,self.spacing,desiredSz)

        return dstate, downsampled_spacing

class LDDMMShootingVectorMomentumImageNet(ShootingVectorMomentumNet):
    """
    Specialization of vector-momentum LDDMM for direct image matching.
    """
    def __init__(self,sz,spacing,params):
        super(LDDMMShootingVectorMomentumImageNet, self).__init__(sz,spacing,params)

    def create_integrator(self):
        """
        Creates integrator to solve EPDiff together with an advevtion equation for the image
        
        :return: returns this integrator 
        """

        cparams = self.params[('forward_model',{},'settings for the forward model')]
        epdiffImage = FM.EPDiffImage( self.sz, self.spacing, self.smoother, cparams )
        return RK.RK4(epdiffImage.f,None,None,cparams)

    def forward(self, I, variables_from_optimizer=None):
        """
        Integrates EPDiff plus advection equation for image forward
        
        :param I: Initial condition for image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the image at time tTo
        """

        mI1 = self.integrator.solve([self.m,I], self.tFrom, self.tTo, variables_from_optimizer)
        return mI1[1]


class LDDMMShootingVectorMomentumImageLoss(RegistrationImageLoss):
    """
    Specialization of the image loss to vector-momentum LDDMM
    """
    def __init__(self,m,sz_sim,spacing_sim,sz_model,spacing_model,params):
        super(LDDMMShootingVectorMomentumImageLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.m = m
        """momentum"""
        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::],self.spacing_model).create_smoother(cparams)
            """smoother to convert from momentum to velocity"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source, variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularzation energy based on the inital momentum
        :param I0_source: not used
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: regularization energy
        """
        m = self.m
        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = self.m.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg

class SVFVectorMomentumImageNet(ShootingVectorMomentumNet):
    """
    Specialization of scalar-momentum LDDMM to SVF image-based matching
    """

    def __init__(self, sz, spacing, params):
        super(SVFVectorMomentumImageNet, self).__init__(sz, spacing, params)

    def create_integrator(self):
        """
        Creates an integrator integrating the scalar momentum conservation law and an advection equation for the image

        :return: returns this integrator
        """
        cparams = self.params[('forward_model', {}, 'settings for the forward model')]

        advection = FM.AdvectImage(self.sz, self.spacing)
        return RK.RK4(advection.f, advection.u, None, cparams)

    def forward(self, I, variables_from_optimizer=None):
        """
        Solved the scalar momentum forward equation and returns the image at time tTo

        :param I: initial image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: image at time tTo
        """
        v = self.smoother.smooth(self.m,None,[I,False],variables_from_optimizer)
        self.integrator.set_pars(v)  # to use this as external parameter
        I1 = self.integrator.solve([I], self.tFrom, self.tTo, variables_from_optimizer)
        return I1[0]

class SVFVectorMomentumImageLoss(RegistrationImageLoss):
    """
    Specialization of the loss to scalar-momentum LDDMM on images
    """

    def __init__(self, m, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(SVFVectorMomentumImageLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.m = m
        """vector momentum"""
        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
            """smoother to go from momentum to velocity"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source, variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularization energy from the initial vector momentum as obtained from the scalar momentum

        :param I0_source: source image
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = self.m

        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = self.m.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()

        return reg


class LDDMMShootingVectorMomentumMapNet(ShootingVectorMomentumNet):
    """
    Specialization for map-based vector-momentum where the map itself is advected
    """
    def __init__(self,sz,spacing,params):
        super(LDDMMShootingVectorMomentumMapNet, self).__init__(sz,spacing,params)


    def create_integrator(self):
        """
        Creates an integrator for EPDiff + advection equation for the map
        
        :return: returns this integrator 
        """
        cparams = self.params[('forward_model',{},'settings for the forward model')]
        epdiffMap = FM.EPDiffMap( self.sz, self.spacing, self.smoother, cparams )
        return RK.RK4(epdiffMap.f,None,None,self.params)

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solves EPDiff + advection equation forward and returns the map at time tTo
        
        :param phi: initial condition for the map 
        :param I0_source: not used
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the map at time tTo
        """

        self.smoother.set_source_image(I0_source)
        mphi1 = self.integrator.solve([self.m,phi], self.tFrom, self.tTo, variables_from_optimizer)
        return mphi1[1]


class LDDMMShootingVectorMomentumMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss for map-based vector momumentum. Image similarity is computed based on warping the source
    image with the advected map.
    """

    def __init__(self,m,sz_sim,spacing_sim,sz_model,spacing_model,params):
        super(LDDMMShootingVectorMomentumMapLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.m = m
        """vector momentum"""

        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
            """smoother to obtain the velocity field from the momentum field"""
            self.use_net = True if cparams['smoother']['type'] == 'adaptiveNet' else False
        else:
            self.develop_smoother = None
            self.use_net = False

    def compute_regularization_energy(self, I0_source, variables_from_forward_model, variables_from_optimizer=None):
        """
        Commputes the regularization energy from the initial vector momentum
        
        :param I0_source: not used 
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = self.m

        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = self.m.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg

class SVFVectorMomentumMapNet(ShootingVectorMomentumNet):
    """
    Specialization of scalar-momentum LDDMM to SVF image-based matching
    """

    def __init__(self, sz, spacing, params):
        super(SVFVectorMomentumMapNet, self).__init__(sz, spacing, params)

    def create_integrator(self):
        """
        Creates an integrator integrating the scalar momentum conservation law and an advection equation for the image

        :return: returns this integrator
        """
        cparams = self.params[('forward_model', {}, 'settings for the forward model')]

        advectionMap = FM.AdvectMap(self.sz, self.spacing)
        return RK.RK4(advectionMap.f, advectionMap.u, None, cparams)

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solved the scalar momentum forward equation and returns the map at time tTo

        :param phi: initial map
        :param I0_source: not used
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: image at time tTo
        """

        v = self.smoother.smooth(self.m,None,[I0_source,False],variables_from_optimizer)
        self.integrator.set_pars(v)  # to use this as external parameter
        phi1 = self.integrator.solve([phi], self.tFrom, self.tTo, variables_from_optimizer)
        return phi1[0]

class SVFVectorMomentumMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss to scalar-momentum LDDMM on images
    """

    def __init__(self, m, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(SVFVectorMomentumMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.m = m
        """vector momentum"""
        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
            """smoother to go from momentum to velocity"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source,variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularization energy from the initial vector momentum as obtained from the scalar momentum

        :param I0_source: source image
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = self.m

        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = self.m.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg

class ShootingScalarMomentumNet(RegistrationNetTimeIntegration):
    """
    Specialization of the registration network to registrations with scalar momentum. Provides an integrator
    and the scalar momentum parameter.
    """
    def __init__(self,sz,spacing,params):
        super(ShootingScalarMomentumNet, self).__init__(sz, spacing, params)
        self.lam = self.create_registration_parameters()
        """scalar momentum"""
        cparams = params[('forward_model', {}, 'settings for the forward model')]
        self.smoother = SF.SmootherFactory(self.sz[2::], self.spacing).create_smoother(cparams)
        """smoother"""
        self._shared_parameters = self._shared_parameters.union(self.smoother.associate_parameters_with_module(self))
        """registers the smoother parameters so that they are optimized over if applicable"""

        if params['forward_model']['smoother']['type'] == 'adaptiveNet':
            self.add_module('mod_smoother', self.smoother.smoother)

        self.integrator = self.create_integrator()
        """integrator to integrate EPDiff and associated equations (for image or map)"""

    def get_custom_optimizer_output_string(self):
        return self.smoother.get_custom_optimizer_output_string()

    def get_custom_optimizer_output_values(self):
        return self.smoother.get_custom_optimizer_output_values()

    def get_variables_to_transfer_to_loss_function(self):
        d = dict()
        d['smoother'] = self.smoother
        return d

    def create_registration_parameters(self):
        """
        Creates the scalar momentum registration parameter
        
        :return: Returns this scalar momentum parameter 
        """
        return utils.create_ND_scalar_field_parameter_multiNC(self.sz[2::], self.nrOfImages, self.nrOfChannels)

    def get_parameter_image_and_name_to_visualize(self,ISource=None):
        """
        Returns an image of the scalar momentum (magnitude over all channels) and 'lambda' as name
        
        :return: Returns tuple (lamda_magnitude,lambda_name) 
        """
        #name = 'lambda'
        #par_image = ((self.lam[:,...]**2).sum(1))**0.5 # assume BxCxXxYxZ format

        name = '|m(lambda,I0)|'
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, ISource, self.sz, self.spacing)
        par_image = ((m[:,...]**2).sum(1))**0.5 # assume BxCxXxYxZ format

        return par_image,name

    def upsample_registration_parameters(self, desiredSz):
        """
        Upsample the scalar momentum
        
        :param desiredSz: desired size to be upsampled to, e.g., [100,50,40] 
        :return: returns a tuple (upsampled_state,upsampled_spacing)
        """

        ustate = self.state_dict().copy()
        sampler = IS.ResampleImage()
        upsampled_lam, upsampled_spacing = sampler.upsample_image_to_size(self.lam, self.spacing, desiredSz)
        ustate['lam'] = upsampled_lam.data

        return ustate,upsampled_spacing

    def downsample_registration_parameters(self, desiredSz):
        """
        Downsample the scalar momentum

        :param desiredSz: desired size to be downsampled to, e.g., [40,20,10] 
        :return: returns a tuple (downsampled_state,downsampled_spacing)
        """

        dstate = self.state_dict().copy()
        sampler = IS.ResampleImage()
        dstate['lam'],downsampled_spacing=sampler.downsample_image_to_size(self.lam,self.spacing,desiredSz)

        return dstate, downsampled_spacing


class SVFScalarMomentumImageNet(ShootingScalarMomentumNet):
    """
    Specialization of scalar-momentum LDDMM to SVF image-based matching
    """

    def __init__(self, sz, spacing, params):
        super(SVFScalarMomentumImageNet, self).__init__(sz, spacing, params)

    def create_integrator(self):
        """
        Creates an integrator integrating the scalar momentum conservation law and an advection equation for the image

        :return: returns this integrator 
        """
        cparams = self.params[('forward_model', {}, 'settings for the forward model')]

        advection = FM.AdvectImage(self.sz, self.spacing)
        return RK.RK4(advection.f, advection.u, None, cparams)

    def forward(self, I, variables_from_optimizer=None):
        """
        Solved the scalar momentum forward equation and returns the image at time tTo

        :param I: initial image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: image at time tTo
        """
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, I, self.sz, self.spacing)
        v = self.smoother.smooth(m,None,[I,False],variables_from_optimizer)
        self.integrator.set_pars(v)  # to use this as external parameter
        I1 = self.integrator.solve([I], self.tFrom, self.tTo, variables_from_optimizer)
        return I1[0]

class SVFScalarMomentumImageLoss(RegistrationImageLoss):
    """
    Specialization of the loss to scalar-momentum LDDMM on images
    """

    def __init__(self, lam, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(SVFScalarMomentumImageLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.lam = lam
        """scalar momentum"""
        if params['similarity_measure'][('develop_mod_on', False, 'developing mode')]:
            cparams = params[('similarity_measure', {}, 'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
            """smoother to go from momentum to velocity"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source,variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularization energy from the initial vector momentum as obtained from the scalar momentum

        :param I0_source: source image 
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, I0_source, self.sz_model, self.spacing_model)

        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = v.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg


class LDDMMShootingScalarMomentumImageNet(ShootingScalarMomentumNet):
    """
    Specialization of scalar-momentum LDDMM to image-based matching
    """
    def __init__(self,sz,spacing,params):
        super(LDDMMShootingScalarMomentumImageNet, self).__init__(sz,spacing,params)

    def create_integrator(self):
        """
        Creates an integrator integrating the scalar momentum conservation law and an advection equation for the image
        
        :return: returns this integrator 
        """
        cparams = self.params[('forward_model',{},'settings for the forward model')]
        epdiffScalarMomentumImage = FM.EPDiffScalarMomentumImage( self.sz, self.spacing, self.smoother, cparams )
        return RK.RK4(epdiffScalarMomentumImage.f,None,None,cparams)

    def forward(self, I, variables_from_optimizer=None):
        """
        Solved the scalar momentum forward equation and returns the image at time tTo
        
        :param I: initial image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: image at time tTo
        """
        lamI1 = self.integrator.solve([self.lam,I], self.tFrom, self.tTo, variables_from_optimizer)
        return lamI1[1]


class LDDMMShootingScalarMomentumImageLoss(RegistrationImageLoss):
    """
    Specialization of the loss to scalar-momentum LDDMM on images
    """
    def __init__(self,lam,sz_sim,spacing_sim,sz_model,spacing_model,params):
        super(LDDMMShootingScalarMomentumImageLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.lam = lam
        """scalar momentum"""
        if params['similarity_measure'][('develop_mod_on', False, 'developing mode')]:
            cparams = params[('similarity_measure', {}, 'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
            """smoother to go from momentum to velocity"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source,variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularization energy from the initial vector momentum as obtained from the scalar momentum
        
        :param I0_source: source image 
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, I0_source, self.sz_model, self.spacing_model)

        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = v.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg


class LDDMMShootingScalarMomentumMapNet(ShootingScalarMomentumNet):
    """
    Specialization of scalar-momentum LDDMM registration to map-based image matching
    """
    def __init__(self,sz,spacing,params):
        super(LDDMMShootingScalarMomentumMapNet, self).__init__(sz,spacing,params)

    def create_integrator(self):
        """
        Creates an integrator integrating the scalar conservation law for the scalar momentum,
        the advection equation for the image and the advection equation for the map,
        
        :return: returns this integrator 
        """
        cparams = self.params[('forward_model',{},'settings for the forward model')]
        epdiffScalarMomentumMap = FM.EPDiffScalarMomentumMap( self.sz, self.spacing, self.smoother, cparams )
        return RK.RK4(epdiffScalarMomentumMap.f,None,None,cparams)

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solves the scalar conservation law and the two advection equations forward in time.
        
        :param phi: initial condition for the map 
        :param I0_source: initial condition for the image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the map at time tTo
        """
        self.smoother.set_source_image(I0_source)
        lamIphi1 = self.integrator.solve([self.lam,I0_source, phi], self.tFrom, self.tTo, variables_from_optimizer)
        return lamIphi1[2]


class LDDMMShootingScalarMomentumMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss function to scalar-momentum LDDMM for maps. 
    """
    def __init__(self,lam,sz_sim,spacing_sim,sz_model,spacing_model,params):
        super(LDDMMShootingScalarMomentumMapLoss, self).__init__(sz_sim,spacing_sim,sz_model,spacing_model,params)
        self.lam = lam
        """scalar momentum"""

        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::],self.spacing_model).create_smoother(cparams)
            """smoother to go from momentum to velocity for development configuration"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source,variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularizaton energy from the initial vector momentum as computed from the scalar momentum
        
        :param I0_source: initial image
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, I0_source, self.sz_model, self.spacing_model)
        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = v.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg

class SVFScalarMomentumMapNet(ShootingScalarMomentumNet):
    """
    Specialization of scalar-momentum LDDMM to SVF image-based matching
    """

    def __init__(self, sz, spacing, params):
        super(SVFScalarMomentumMapNet, self).__init__(sz, spacing, params)

    def create_integrator(self):
        """
        Creates an integrator integrating the scalar momentum conservation law and an advection equation for the image

        :return: returns this integrator
        """
        cparams = self.params[('forward_model', {}, 'settings for the forward model')]

        advectionMap = FM.AdvectMap(self.sz, self.spacing)
        return RK.RK4(advectionMap.f, advectionMap.u, None, cparams)

    def forward(self, phi, I0_source, variables_from_optimizer=None):
        """
        Solved the scalar momentum forward equation and returns the map at time tTo

        :param I: initial image
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: image at time tTo
        """
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, I0_source, self.sz, self.spacing)
        v = self.smoother.smooth(m,None,[I0_source,False],variables_from_optimizer)
        self.integrator.set_pars(v)  # to use this as external parameter
        phi1 = self.integrator.solve([phi], self.tFrom, self.tTo, variables_from_optimizer)
        return phi1[0]

class SVFScalarMomentumMapLoss(RegistrationMapLoss):
    """
    Specialization of the loss to scalar-momentum LDDMM on images
    """

    def __init__(self, lam, sz_sim, spacing_sim, sz_model, spacing_model, params):
        super(SVFScalarMomentumMapLoss, self).__init__(sz_sim, spacing_sim, sz_model, spacing_model, params)
        self.lam = lam
        """scalar momentum"""

        if params['similarity_measure'][('develop_mod_on',False,'developing mode')]:
            cparams = params[('similarity_measure',{},'settings for the similarity ')]
            self.develop_smoother = SF.SmootherFactory(self.sz_model[2::], self.spacing_model).create_smoother(cparams)
            """smoother to go from momentum to velocity"""
        else:
            self.develop_smoother = None

    def compute_regularization_energy(self, I0_source,variables_from_forward_model, variables_from_optimizer=None):
        """
        Computes the regularization energy from the initial vector momentum as obtained from the scalar momentum

        :param I0_source: source image
        :param variables_from_forward_model: allows passing in additional variables (intended to pass variables between the forward modell and the loss function)
        :param variables_from_optimizer: allows passing variables (as a dict from the optimizer; e.g., the current iteration)
        :return: returns the regularization energy
        """
        m = utils.compute_vector_momentum_from_scalar_momentum_multiNC(self.lam, I0_source, self.sz_model, self.spacing_model)
        if self.develop_smoother is not None:
            v = self.develop_smoother.smooth(m)
        else:
            v = variables_from_forward_model['smoother'].smooth(m,None,[I0_source,False],variables_from_optimizer)

        batch_size = v.size()[0]
        reg = (v * m).sum() * self.spacing_model.prod()/batch_size + variables_from_forward_model['smoother'].get_penalty()
        return reg
