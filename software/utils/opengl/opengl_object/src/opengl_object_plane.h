#ifndef OPENGL_OPENGL_OBJECT_PLANE_H
#define OPENGL_OPENGL_OBJECT_PLANE_H

#include <iostream>
#include <GL/gl.h>

#include <Eigen/Dense>

#include "opengl/opengl_object.h"

namespace opengl {
  class OpenGL_Object_Plane: public OpenGL_Object {
  public:
    OpenGL_Object_Plane( std::string id = "N/A", const KDL::Frame& transform = KDL::Frame::Identity(), const KDL::Frame& offset = KDL::Frame::Identity(), Eigen::Vector2f dimensions = Eigen::Vector2f( 1000.0, 1000.0 ) );
    virtual ~OpenGL_Object_Plane();
    OpenGL_Object_Plane( const OpenGL_Object_Plane& other );
    OpenGL_Object_Plane& operator=( const OpenGL_Object_Plane& other );
  
    void set( Eigen::Vector2f dimensions );
    void set( KDL::Frame transform, Eigen::Vector2f dimensions );
    virtual void set_color( Eigen::Vector3f color );
    virtual void set_color( Eigen::Vector4f color );

    virtual void draw( void );
    virtual void draw( Eigen::Vector3f color );

    Eigen::Vector2f dimensions( void )const;

  protected:
    bool _generate_dl( void );
    void _draw_plane( Eigen::Vector3f color );

    Eigen::Vector2f _dimensions;
    GLuint _dl;
    
  private:

  };
  std::ostream& operator<<( std::ostream& out, const OpenGL_Object_Plane& other );
}

#endif /* OPENGL_OPENGL_OBJECT_PLANE_H */
