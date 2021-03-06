from __future__ import division
import os
import time
from glob import glob
import tensorflow as tf
import numpy as np
from collections import namedtuple

from module import *
from utils import *


class cyclegan(object):
    def __init__(self, sess, args):
        self.sess = sess
        self.batch_size = args.batch_size
        self.image_size = args.fine_size
        self.input_c_dim = args.input_nc
        self.output_c_dim = args.output_nc
        self.L1_lambda = args.L1_lambda
        self.dataset_dir = args.dataset_dir
        self.style_weight = args.style_weight
        self.add_noise = args.add_noise
        

        self.discriminator = discriminator

        if args.use_resnet:
            self.generator = generator_resnet
        else:
            self.generator = generator_unet
        if args.use_lsgan:
            self.criterionGAN = mae_criterion
        else:
            self.criterionGAN = sce_criterion

        self.generator_B2A = generator_B2A
        self.generator_A2B = generator_A2B

        OPTIONS = namedtuple('OPTIONS', 'batch_size image_size \
                              gf_dim df_dim input_c_dim output_c_dim output_style_dim \
                              use_bn is_training')
        self.options = OPTIONS._make((args.batch_size, args.fine_size,
                                      args.ngf, args.ndf, args.input_nc, args.output_nc,
                                      args.output_style_dim, args.normalization, args.phase == 'train'))

        self._build_model()
        self.saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=100)
        self.pool = ImagePool(args.max_size)

    def _build_model(self):
        self.real_data = tf.placeholder(tf.float32,
                                        [None, self.image_size, self.image_size,
                                         self.input_c_dim + self.output_c_dim],
                                        name='real_A_and_B_images')

        # A: MNIST 
        # B: SVHN
        self.real_A = self.real_data[:, :, :, :self.input_c_dim]
        self.real_B = self.real_data[:, :, :, self.input_c_dim: self.input_c_dim + self.output_c_dim]

        self.is_training = tf.placeholder(tf.bool, [], name='is_training')

        #S -> M' + style' -> S_hat
        #real_B -> fake_A + fake_B_style -> fake_B_
        self.fake_A, self.fake_B_style = self.generator_B2A(self.real_B, self.options, False, is_training=self.is_training, name="generatorB2A")
        self.noise = tf.zeros(tf.shape(self.fake_A), dtype=tf.float32)
        if self.add_noise and self.is_training is True:
            self.noise = tf.random_normal(tf.shape(self.fake_A), mean=0.0, stddev=0.1, dtype=tf.float32)
        self.fake_B_ = self.generator_A2B(self.fake_A + self.noise, self.fake_B_style, self.options, False, is_training=self.is_training, name="generatorA2B")

        #M + style' -> S'-> M_hat + style_hat
        #real_A + fake_B_style -> fake_B -> fake_A_ + fake_B_style_
        self.fake_B = self.generator_A2B(self.real_A, self.fake_B_style, self.options, True, is_training=self.is_training, name="generatorA2B")
        self.noise = tf.zeros(tf.shape(self.fake_B), dtype=tf.float32)
        if self.add_noise and self.is_training is True:
            self.noise = tf.random_normal(shape=tf.shape(self.fake_B), mean=0.0, stddev=0.1, dtype=tf.float32)
        self.fake_A_, self.fake_B_style_ = self.generator_B2A(self.fake_B + self.noise, self.options, True, is_training=self.is_training, name="generatorB2A")
        

        self.DB_fake = self.discriminator(self.fake_B, self.options, reuse=False, is_training=self.is_training, name="discriminatorB")
        self.DA_fake = self.discriminator(self.fake_A, self.options, reuse=False, is_training=self.is_training, name="discriminatorA")
        self.g_loss_a2b = self.criterionGAN(self.DB_fake, tf.ones_like(self.DB_fake)) \
            + self.L1_lambda * abs_criterion(self.real_A, self.fake_A_) \
            + self.L1_lambda * abs_criterion(self.real_B, self.fake_B_)
        self.g_loss_b2a = self.criterionGAN(self.DA_fake, tf.ones_like(self.DA_fake)) \
            + self.L1_lambda * abs_criterion(self.real_A, self.fake_A_) \
            + self.L1_lambda * abs_criterion(self.real_B, self.fake_B_)
        self.g_loss_style = self.style_weight * abs_criterion(self.fake_B_style, self.fake_B_style_)
        self.g_loss = self.criterionGAN(self.DA_fake, tf.ones_like(self.DA_fake)) \
            + self.criterionGAN(self.DB_fake, tf.ones_like(self.DB_fake)) \
            + self.L1_lambda * abs_criterion(self.real_A, self.fake_A_) \
            + self.L1_lambda * abs_criterion(self.real_B, self.fake_B_) \
            + self.style_weight * abs_criterion(self.fake_B_style, self.fake_B_style_)

        self.fake_A_sample = tf.placeholder(tf.float32,
                                            [None, self.image_size, self.image_size,
                                             self.input_c_dim], name='fake_A_sample')
        self.fake_B_sample = tf.placeholder(tf.float32,
                                            [None, self.image_size, self.image_size,
                                             self.output_c_dim], name='fake_B_sample')
        self.DB_real = self.discriminator(self.real_B, self.options, reuse=True, is_training=self.is_training, name="discriminatorB")
        self.DA_real = self.discriminator(self.real_A, self.options, reuse=True, is_training=self.is_training, name="discriminatorA")
        self.DB_fake_sample = self.discriminator(self.fake_B_sample, self.options, reuse=True, is_training=self.is_training, name="discriminatorB")
        self.DA_fake_sample = self.discriminator(self.fake_A_sample, self.options, reuse=True, is_training=self.is_training, name="discriminatorA")

        self.db_loss_real = self.criterionGAN(self.DB_real, tf.ones_like(self.DB_real))
        self.db_loss_fake = self.criterionGAN(self.DB_fake_sample, tf.zeros_like(self.DB_fake_sample))
        self.db_loss = (self.db_loss_real + self.db_loss_fake) / 2
        self.da_loss_real = self.criterionGAN(self.DA_real, tf.ones_like(self.DA_real))
        self.da_loss_fake = self.criterionGAN(self.DA_fake_sample, tf.zeros_like(self.DA_fake_sample))
        self.da_loss = (self.da_loss_real + self.da_loss_fake) / 2
        self.d_loss = self.da_loss + self.db_loss

       

        self.g_loss_a2b_sum = tf.summary.scalar("g_loss_a2b", self.g_loss_a2b)
        self.g_loss_b2a_sum = tf.summary.scalar("g_loss_b2a", self.g_loss_b2a)
        self.g_loss_style_sum = tf.summary.scalar("g_loss_style", self.g_loss_style)
        self.g_loss_sum = tf.summary.scalar("g_loss", self.g_loss)
        self.g_sum = tf.summary.merge([self.g_loss_a2b_sum, self.g_loss_b2a_sum, self.g_loss_style_sum, self.g_loss_sum])

        self.db_loss_sum = tf.summary.scalar("db_loss", self.db_loss)
        self.da_loss_sum = tf.summary.scalar("da_loss", self.da_loss)
        self.d_loss_sum = tf.summary.scalar("d_loss", self.d_loss)
        self.db_loss_real_sum = tf.summary.scalar("db_loss_real", self.db_loss_real)
        self.db_loss_fake_sum = tf.summary.scalar("db_loss_fake", self.db_loss_fake)
        self.da_loss_real_sum = tf.summary.scalar("da_loss_real", self.da_loss_real)
        self.da_loss_fake_sum = tf.summary.scalar("da_loss_fake", self.da_loss_fake)
        self.d_sum = tf.summary.merge(
            [self.da_loss_sum, self.da_loss_real_sum, self.da_loss_fake_sum,
             self.db_loss_sum, self.db_loss_real_sum, self.db_loss_fake_sum,
             self.d_loss_sum]
        )

        self.test_A = tf.placeholder(tf.float32,
                                     [None, self.image_size, self.image_size,
                                      self.input_c_dim], name='test_A')
        self.test_B = tf.placeholder(tf.float32,
                                     [None, self.image_size, self.image_size,
                                      self.output_c_dim], name='test_B')
        
        self.testA, self.test_B_style = self.generator_B2A(self.test_B, self.options, True, is_training=self.is_training, name="generatorB2A")
        #self.testB = self.generator_A2B(self.test_A, self.test_B_style, self.options, True, is_training=self.is_training, name="generatorA2B")
        
        t_vars = tf.trainable_variables()
        self.d_vars = [var for var in t_vars if 'discriminator' in var.name]
        self.g_vars = [var for var in t_vars if 'generator' in var.name]

        for var in t_vars: print(var.name)

    def train(self, args):
        """Train cyclegan"""
        self.lr = tf.placeholder(tf.float32, None, name='learning_rate')
        self.g_optim = tf.train.AdamOptimizer(self.lr, beta1=args.beta1) \
            .minimize(self.g_loss, var_list=self.g_vars)
        self.d_optim = tf.train.AdamOptimizer(self.lr, beta1=args.beta1) \
            .minimize(self.d_loss, var_list=self.d_vars)

        init_op = tf.global_variables_initializer()
        self.sess.run(init_op)

        self.writer = tf.summary.FileWriter(args.log_dir, self.sess.graph)

        counter = 1
        start_time = time.time()

        if args.continue_train:
            if self.load(args.checkpoint_dir):
                print(" [*] Load SUCCESS")
            else:
                print(" [!] Load failed...")

        for epoch in range(args.epoch):
            dataA = glob(self.dataset_dir + '/trainA/*/*.png')
            dataB = glob(self.dataset_dir + '/trainB/*.png')
            np.random.shuffle(dataA)
            np.random.shuffle(dataB)
            batch_idxs = min(min(len(dataA), len(dataB)), args.train_size) // self.batch_size
            lr = args.lr if epoch < args.epoch_step else args.lr*(args.epoch-epoch)/(args.epoch-args.epoch_step)

            for idx in range(0, batch_idxs):
                batch_files = list(zip(dataA[idx * self.batch_size:(idx + 1) * self.batch_size],
                                       dataB[idx * self.batch_size:(idx + 1) * self.batch_size]))
                batch_images = [load_train_data(batch_file, args.load_size, args.fine_size) for batch_file in batch_files]
                batch_images = np.array(batch_images).astype(np.float32)

                # Update G network and record fake outputs
                fake_A, fake_B, _, summary_str = self.sess.run(
                    [self.fake_A, self.fake_B, self.g_optim, self.g_sum],
                    feed_dict={self.real_data: batch_images, self.lr: lr, self.is_training:True})
                self.writer.add_summary(summary_str, counter)
                [fake_A, fake_B] = self.pool([fake_A, fake_B])

                # Update D network
                _, summary_str = self.sess.run(
                    [self.d_optim, self.d_sum],
                    feed_dict={self.real_data: batch_images,
                               self.fake_A_sample: fake_A,
                               self.fake_B_sample: fake_B,
                               self.is_training: True,
                               self.lr: lr})
                self.writer.add_summary(summary_str, counter)

                counter += 1

                if np.mod(counter, args.print_freq) == 1:
                    print(("Epoch: [%2d] [%4d/%4d] time: %4.4f" % (
                        epoch, idx, batch_idxs, time.time() - start_time)))
                    self.sample_model(args.sample_dir, epoch, idx, args)

                if np.mod(counter, args.save_freq) == 2:
                    self.save(args.checkpoint_dir, counter)

    def save(self, checkpoint_dir, step):
        model_name = "cyclegan.model"
        self.saver.save(self.sess,
                        os.path.join(checkpoint_dir, model_name),
                        global_step=step)

    def load(self, checkpoint_dir):
        print(" [*] Reading checkpoint...")
        ckpt = tf.train.get_checkpoint_state(checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
            self.saver.restore(self.sess, os.path.join(checkpoint_dir, ckpt_name))
            return True
        else:
            return False

    def sample_model(self, sample_dir, epoch, idx, args):
        dataA = glob(self.dataset_dir + '/testA/*/*.png')
        dataB = glob(self.dataset_dir + '/testB/*.png')
        np.random.shuffle(dataA)
        np.random.shuffle(dataB)
        batch_files = list(zip(dataA[:self.batch_size], dataB[:self.batch_size]))
        sample_images = [load_train_data(batch_file, load_size=args.load_size, fine_size=args.fine_size, is_testing=True) for batch_file in batch_files]
        sample_images = np.array(sample_images).astype(np.float32)

        real_A, fake_A, recon_A, real_B, fake_B, recon_B = self.sess.run(
            [self.real_A, self.fake_A, self.fake_A_, self.real_B, self.fake_B, self.fake_B_],
            feed_dict={self.real_data: sample_images, self.is_training:False}
        )
        w = 16
        h = int(self.batch_size / w)
        save_images(fake_A, [h, w],
                    './{}/fakeA_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(fake_B, [h, w],
                    './{}/fakeB_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(real_A, [h, w],
                    './{}/realA_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(real_B, [h, w],
                    './{}/realB_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(recon_A, [h, w],
                    './{}/reconA_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
        save_images(recon_B, [h, w],
                    './{}/reconB_{:02d}_{:04d}.jpg'.format(sample_dir, epoch, idx))
    


    
    def test(self, args):
        """Test cyclegan"""
        init_op = tf.global_variables_initializer()
        self.sess.run(init_op)
        if args.which_direction == 'AtoB':
            sample_files = glob(self.dataset_dir + '/testA/*/*.png')
        elif args.which_direction == 'BtoA':
            sample_files = glob(self.dataset_dir + '/testB/*.png')
        else:
            raise Exception('--which_direction must be AtoB or BtoA')

        if self.load(args.checkpoint_dir):
            print(" [*] Load SUCCESS")
        else:
            print(" [!] Load failed...")

        # write html for visual comparison
        index_path = os.path.join(args.test_dir, '{0}_index.html'.format(args.which_direction))
        index = open(index_path, "w")
        index.write("<html><body><table><tr>")
        index.write("<th>name</th><th>input</th><th>output</th></tr>")

        out_var, in_var = (self.testB, self.test_A) if args.which_direction == 'AtoB' else (
            self.testA, self.test_B)

        for sample_file in sample_files:
            print('Processing image: ' + sample_file)
            sample_image = [load_test_data(image_path=sample_file, is_gray_scale=(args.which_direction == 'AtoB'), fine_size=args.fine_size)]
            sample_image = np.array(sample_image).astype(np.float32)
            if len(sample_image.shape) is 3:
                sample_image = np.expand_dims(sample_image, axis = 3)
            if args.which_direction == 'AtoB':
                subfolder_name = os.path.split(os.path.dirname(sample_file))[-1]
                image_folder_path = os.path.join(args.test_dir, subfolder_name)
                if not os.path.exists(image_folder_path):
                    os.mkdir(image_folder_path)
                image_path = os.path.join(image_folder_path,
                                      '{0}'.format(os.path.basename(sample_file)))
            else:
                image_path = os.path.join(args.test_dir,
                                          '{0}'.format(os.path.basename(sample_file)))
            fake_img = self.sess.run(out_var, feed_dict={in_var: sample_image, self.is_training:False})
            save_images(fake_img, [1, 1], image_path)
            index.write("<td>%s</td>" % os.path.basename(image_path))
            index.write("<td><img src='%s'></td>" % (sample_file if os.path.isabs(sample_file) else (
                '..' + os.path.sep + sample_file)))
            index.write("<td><img src='%s'></td>" % (image_path if os.path.isabs(image_path) else (
                '..' + os.path.sep + image_path)))
            index.write("</tr>")
        index.close()

